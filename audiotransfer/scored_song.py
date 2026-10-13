"""Align a reviewed melody to a recording; keep baseline instrumental passages.

This optional workflow consumes an explicit, song-specific score. It never
pretends to infer a score, a key, or a repeated chorus from arbitrary audio.
"""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

import numpy as np

from .events import NoteEvent
from .notes import midi_to_frequency, midi_to_note_name, frequency_to_midi
from .output import write_outputs


def parse_template(profile: dict, name: str) -> list[tuple[int | None, float]]:
    result = []
    previous_tie = False
    scale = (0, 2, 4, 5, 7, 9, 11)
    for bar_name in profile['templates'][name]:
        bar_beats = 0.0
        for token in profile['bars'][bar_name].split():
            label, length = token.split(':')
            tie = length.endswith('~')
            beats = float(length.rstrip('~'))
            if not math.isfinite(beats) or beats <= 0:
                raise ValueError('音符拍数必须为正数')
            bar_beats += beats
            degree = int(label[0])
            if not 0 <= degree <= 7 or any(c not in "'," for c in label[1:]):
                raise ValueError(f'无效简谱音：{label}')
            midi = None if degree == 0 else (int(profile['tonic_midi']) + scale[degree - 1]
                        + 12 * (label.count("'") - label.count(',')))
            if midi is not None and not 0 <= midi <= 127:
                raise ValueError('音符超出MIDI音域')
            if previous_tie:
                if not result or result[-1][0] != midi or midi is None:
                    raise ValueError('延音线两端必须为同一音高')
                result[-1] = (midi, result[-1][1] + beats)
            else:
                result.append((midi, beats))
            previous_tie = tie
        if abs(bar_beats - 4) > 1e-6:
            raise ValueError(f'{bar_name}不是四拍小节：{bar_beats}')
    if previous_tie:
        raise ValueError('末尾延音线没有终点')
    return result


def fit_section(notes, start_hint, pitch, confidence, hop_seconds):
    """Fit offset/tempo to reliable centres, leaving slide edges unscored.

    Equal pitches remain separate attacks in the score. They do not need an
    artificial f0 change to establish the next sung syllable.
    """
    starts = np.cumsum([0] + [b for _, b in notes])
    # Interior samples avoid evaluating the f0 of consonants or slides.
    beat_positions, targets = [], []
    for index, (midi, length) in enumerate(notes):
        if midi is not None:
            sample = np.arange(starts[index] + min(.16, length * .25),
                               starts[index + 1] - min(.12, length * .2), .12)
            beat_positions.extend(sample)
            targets.extend([midi] * len(sample))
    beat_positions = np.asarray(beat_positions)
    targets = np.asarray(targets)
    midi_pitch = np.full(len(pitch), np.nan)
    voiced = np.isfinite(pitch) & (pitch > 0)
    midi_pitch[voiced] = 69 + 12 * np.log2(pitch[voiced] / 440)
    best = None
    for quarter in np.arange(.528, .5441, .0005):
        for offset in np.arange(start_hint - .4, start_hint + .401, .01):
            indices = np.rint((offset + beat_positions * quarter) / hop_seconds).astype(int)
            if np.any(indices < 0) or np.any(indices >= len(pitch)):
                continue
            valid = voiced[indices] & (confidence[indices] >= .5)
            coverage = float(np.mean(valid))
            if coverage < .35:
                continue
            distance = np.abs(midi_pitch[indices[valid]] - targets[valid])
            weight = confidence[indices[valid]]
            error = float(np.average(np.minimum(distance, 4) ** 2, weights=weight))
            # Short, repeated-note phrases can admit several timing fits.
            # A weak 112-BPM prior prevents unconstrained tempo drift; the
            # search remains free to follow the recording's local tempo.
            tempo_prior = .01 * ((quarter - 60 / 112) / .004) ** 2
            cost = error + .3 * (1 - coverage) + .01 * abs(offset - start_hint) + tempo_prior
            candidate = (cost, float(offset), float(quarter), coverage,
                         float(np.average(distance <= .65, weights=weight)))
            if best is None or candidate[0] < best[0]:
                best = candidate
    if best is None:
        raise ValueError('原曲中没有足够的可靠音高，无法对齐校谱')
    _, offset, quarter, coverage, agreement = best
    return offset, quarter, {'reliable_coverage': round(coverage, 3),
                            'pitch_agreement_65_cents': round(agreement, 3),
                            'fit_error': round(best[0], 3)}


def score_events(notes, offset, quarter, transpose=0, legato=False):
    boundaries = np.rint((offset + np.cumsum([0] + [b for _, b in notes]) * quarter) * 1000).astype(int)
    events, velocities = [], []
    for i, (midi, beats) in enumerate(notes):
        if midi is None:
            events.append(NoteEvent(0, int(boundaries[i + 1] - boundaries[i]), 'rest'))
            velocities.append(88)
        else:
            note = midi + transpose
            if not 0 <= note <= 127:
                raise ValueError('移调后超出MIDI音域')
            # Score entries are real attacks; tied notes have already merged.
            continues = legato and i > 0 and notes[i - 1][0] is not None and notes[i - 1][0] != midi
            events.append(NoteEvent(midi_to_frequency(note), int(boundaries[i + 1] - boundaries[i]),
                                    midi_to_note_name(note), continues))
            beat = sum(length for _, length in notes[:i])
            velocity = 98 if abs(beat % 4) < .01 else 91 if abs(beat % 1) < .01 else 85
            velocities.append(velocity)
    return int(boundaries[0]), int(boundaries[-1]), events, velocities


def baseline_slice(baseline, start_ms, end_ms, shift):
    events, velocities = [], []
    elapsed = 0
    for item in baseline:
        end = elapsed + int(item['duration_ms'])
        left, right = max(start_ms, elapsed), min(end_ms, end)
        if right > left:
            hz = float(item['frequency_hz'])
            if hz > 0:
                midi = frequency_to_midi(hz) + shift
                hz, name = midi_to_frequency(midi), midi_to_note_name(midi)
            else:
                name = 'rest'
            legato = bool(events) and left == elapsed and bool(item.get('legato', False))
            events.append(NoteEvent(hz, right - left, name, legato))
            velocities.append(int(item.get('velocity', 90)))
        elapsed = end
    return events, velocities


def accompaniment_gap(profile, baseline, start_ms, end_ms, shift):
    """A reviewed score may explicitly mark backing-only gaps as rests."""
    ranges = profile.get('keep_baseline_ranges_ms')
    if ranges is None:
        return baseline_slice(baseline, start_ms, end_ms, shift)
    events, velocities = [], []
    cursor = start_ms
    for left, right in sorted(ranges):
        left, right = max(start_ms, int(left)), min(end_ms, int(right))
        if right <= left:
            continue
        if left < cursor:
            raise ValueError('保留伴奏片段的时间范围不能重叠')
        if left > cursor:
            events.append(NoteEvent(0, left - cursor, 'rest')); velocities.append(88)
        music, gains = baseline_slice(baseline, left, right, shift)
        events.extend(music); velocities.extend(gains)
        cursor = right
    if cursor < end_ms:
        events.append(NoteEvent(0, end_ms - cursor, 'rest')); velocities.append(88)
    return events, velocities


def build(profile, baseline, analysis, transpose=0):
    if not -36 <= transpose <= 36:
        raise ValueError('移调必须在-36到36个半音之间')
    pitch, confidence = analysis['pitch'], analysis['confidence']
    total = sum(int(e['duration_ms']) for e in baseline)
    sample_count = int(analysis['sample_count'])
    sample_rate = int(analysis['sample_rate'])
    # Frame timestamps follow the configured hop, not duration/n_frames.
    hop = float(profile.get('hop_seconds', .02))
    if abs(sample_count / sample_rate * 1000 - total) > 1:
        raise ValueError('校谱分析与底稿不是同一长度的原曲')
    if abs(total - int(profile.get('expected_duration_ms', total))) > 1000:
        raise ValueError('这份校谱对应另一版本的原曲，时长不匹配')
    events, velocities, report = [], [], []
    cursor = 0
    for section_index, section in enumerate(profile['sections']):
        section_profile = {**profile, 'tonic_midi': section.get('tonic_midi', profile['tonic_midi'])}
        notes = parse_template(section_profile, section['template'])
        reviewed = section.get('reviewed_alignment')
        if reviewed is not None:
            # Polyphonic instrument f0 may follow a chord or an overtone.
            # An explicit reviewed alignment uses recording-derived timing;
            # do not mislabel its spectral evidence as monophonic f0 accuracy.
            if not isinstance(reviewed, dict):
                raise ValueError('已校对的对齐设置必须是对象')
            offset = float(reviewed['offset_seconds'])
            quarter = float(reviewed['quarter_seconds'])
            if not math.isfinite(offset) or offset < 0 or not math.isfinite(quarter) or quarter <= 0:
                raise ValueError('已校对的起点和拍长必须是有限有效数值')
            fit = {'alignment_method': 'reviewed_recording_alignment',
                   'reviewed_alignment_evidence': reviewed.get('evidence')}
        else:
            offset, quarter, fit = fit_section(notes, section['start_hint'], pitch, confidence, hop)
            if fit['pitch_agreement_65_cents'] < .55 or fit['fit_error'] > 3:
                raise ValueError(f"{section['name']}音高与校谱不匹配，请检查原曲版本及调号")
        start, end, corrected, strengths = score_events(notes, offset, quarter, transpose,
                                                       legato=section.get('source') == 'instruments')
        # Independent fits may overlap slightly in the notated pickup rest.
        # Trim only that rest, never a sounded note or the previous release.
        if start < cursor and corrected[0].frequency_hz == 0 and cursor - start < min(150, corrected[0].duration_ms):
            trim = cursor - start
            first = corrected[0]
            corrected[0] = NoteEvent(0, first.duration_ms - trim, 'rest')
            start = cursor
            fit['pickup_rest_trimmed_ms'] = trim
        # A singer can hold the last note past the printed bar line. Retain
        # that hold when the original vocal remains on the same pitch. Do
        # not extend across the next verse/chorus or into an instrument.
        next_hint = profile['sections'][section_index + 1]['start_hint'] if section_index + 1 < len(profile['sections']) else total / 1000
        if section.get('source', 'vocals') == 'vocals' and next_hint - end / 1000 > .5 and corrected[-1].frequency_hz > 0:
            target = notes[-1][0]
            last_good = end
            bad_frames = 0
            for frame_index in range(math.ceil(end / (hop * 1000)), min(len(pitch), math.floor((end + 1600) / (hop * 1000)))):
                hz = pitch[frame_index]
                is_vocal = 'sources' not in analysis or str(analysis['sources'][frame_index]) == 'vocals'
                matches = is_vocal and hz > 0 and confidence[frame_index] >= .35 and abs(69 + 12 * math.log2(hz / 440) - target) <= .8
                if matches:
                    last_good = round((frame_index + 1) * hop * 1000)
                    bad_frames = 0
                else:
                    bad_frames += 1
                    if bad_frames * hop >= .08:
                        break
            extension = max(0, last_good - end)
            if extension:
                last = corrected[-1]
                corrected[-1] = NoteEvent(last.frequency_hz, last.duration_ms + extension, last.note)
                end += extension
                fit['final_vocal_hold_ms'] = extension
        if start < cursor or end > total:
            raise ValueError(f"{section['name']}与相邻段落重叠或超出原曲")
        before, gains = accompaniment_gap(profile, baseline, cursor, start, transpose - int(profile['baseline_transpose']))
        events.extend(before); velocities.extend(gains)
        events.extend(corrected); velocities.extend(strengths)
        cursor = end
        report.append({'name': section['name'], 'start_ms': start, 'end_ms': end,
                       'quarter_ms': round(quarter * 1000, 3), 'notes': len(corrected), **fit})
    tail, gains = accompaniment_gap(profile, baseline, cursor, total, transpose - int(profile['baseline_transpose']))
    events.extend(tail); velocities.extend(gains)
    assert sum(e.duration_ms for e in events) == total
    return events, velocities, {'title': profile['title'], 'duration_ms': total,
                               'transpose_semitones': transpose, 'tempo_bpm': 112,
                               'method': 'reviewed_song_score_aligned_to_recording',
                               'score_reference': profile.get('reference_url'),
                               'sections': report, 'legato_transitions': sum(e.legato for e in events)}


def main(argv=None):
    parser = argparse.ArgumentParser(description='把这首歌的已校对旋律与原曲对齐；保留底稿中的前奏间奏尾奏。')
    parser.add_argument('--profile', type=Path, required=True)
    parser.add_argument('--baseline', type=Path, required=True)
    parser.add_argument('--analysis', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--transpose', type=int, default=0)
    parser.add_argument('--sound-mode', choices=['soft', 'harmonic', 'pulse', 'classic'], default='soft')
    parser.add_argument('--active-low', action='store_true')
    args = parser.parse_args(argv)
    profile = json.loads(args.profile.read_text(encoding='utf-8'))
    baseline = json.loads(args.baseline.read_text(encoding='utf-8'))
    with np.load(args.analysis, allow_pickle=False) as analysis:
        events, velocities, metadata = build(profile, baseline, analysis, args.transpose)
    write_outputs(events, args.output, target='platformio-stm32', stm32_pin='PA8',
                  gap_ms=12, sound_mode=args.sound_mode, active_low=args.active_low,
                  velocities=velocities, arrangement=metadata)
    (args.output / 'alignment.json').write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(metadata, ensure_ascii=False, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
