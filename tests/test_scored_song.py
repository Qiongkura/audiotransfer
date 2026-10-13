import unittest
import numpy as np

from audiotransfer.scored_song import parse_template, score_events, fit_section, baseline_slice, build, accompaniment_gap
from audiotransfer.notes import midi_to_frequency


class ReviewedSongTests(unittest.TestCase):
    def test_ties_continue_across_bar_but_lyric_repetitions_reattack(self):
        profile = {'tonic_midi':57, 'templates':{'x':['a','b']},
                   'bars':{'a':"1:1 1:1 2:2~", 'b':"2:1 3:3"}}
        notes = parse_template(profile, 'x')
        self.assertEqual(notes, [(57,1),(57,1),(59,3),(61,3)])
        start,end,events,_ = score_events(notes, .025, .535)
        self.assertEqual((start,end), (25,4305))
        self.assertEqual(sum(e.duration_ms for e in events), 4280)
        self.assertFalse(events[1].legato)
        self.assertEqual(events[2].duration_ms,1605)

    def test_alignment_recovers_timing_with_consonant_gaps_and_slide_edges(self):
        notes = [(None,.5),(64,.5),(64,1),(66,1),(71,2),(66,1),(64,2)]
        offset, quarter, hop = .92, .536, .02
        pitch = np.zeros(300); confidence = np.zeros(300)
        cursor = offset
        for midi, beats in notes:
            first,last = round(cursor/hop), round((cursor+beats*quarter)/hop)
            if midi:
                pitch[first:last] = midi_to_frequency(midi)
                confidence[first:last] = .9
                pitch[first:first+3] *= 2**(-.9/12)  # slide onset
                pitch[first:first+1] = 0  # consonant
            cursor += beats*quarter
        found, period, report = fit_section(notes, 1, pitch, confidence, hop)
        # Interior-only fitting intentionally leaves consonant/slide edges
        # unconstrained; require synchronization within an 80-ms articulation.
        self.assertLess(abs(found-offset), .081)
        self.assertLess(abs(period-quarter), .002)
        self.assertGreater(report['pitch_agreement_65_cents'], .9)

    def test_slicing_cannot_carry_legato_from_omitted_baseline_note(self):
        baseline = [{'frequency_hz':440,'duration_ms':200,'legato':False},
                    {'frequency_hz':493.883301,'duration_ms':300,'legato':True}]
        events,_ = baseline_slice(baseline, 210, 450, -12)
        self.assertEqual(sum(e.duration_ms for e in events), 240)
        self.assertAlmostEqual(events[0].frequency_hz, 246.94165, places=3)
        self.assertFalse(events[0].legato)

    def test_broken_tie_or_incomplete_bar_is_rejected(self):
        profile = {'tonic_midi':57, 'templates':{'x':['a','b']},
                   'bars':{'a':'1:4~','b':'2:4'}}
        with self.assertRaises(ValueError): parse_template(profile,'x')
        profile['bars']['a']='1:3'
        with self.assertRaises(ValueError): parse_template(profile,'x')

    def test_wrong_recording_duration_is_rejected_before_fit(self):
        profile = {'expected_duration_ms':10000}
        analysis = {'pitch':np.zeros(50),'confidence':np.zeros(50),'sample_count':16000,'sample_rate':16000}
        with self.assertRaisesRegex(ValueError, '另一版本'):
            build(profile,[{'frequency_hz':0,'duration_ms':1000}],analysis)

    def test_accompaniment_only_gaps_rest_except_explicit_instrument_passage(self):
        baseline = [{'frequency_hz':440,'duration_ms':1000}]
        events,_ = accompaniment_gap({'keep_baseline_ranges_ms':[[300,700]]}, baseline, 100, 900, 0)
        self.assertEqual([e.duration_ms for e in events], [200,400,200])
        self.assertEqual([e.frequency_hz > 0 for e in events], [False,True,False])

    def _reviewed_instrument_profile(self):
        profile = {'title':'reviewed test', 'tonic_midi':69, 'expected_duration_ms':2000, 'baseline_transpose':0,
                   'keep_baseline_ranges_ms':[], 'bars':{'a':'1:4'}, 'templates':{'x':['a']},
                   'sections':[{'name':'verified instrument','source':'instruments','template':'x',
                                'reviewed_alignment':{'offset_seconds':.5,'quarter_seconds':.25}}]}
        analysis = {'pitch':np.zeros(100),'confidence':np.zeros(100),
                    'sample_count':32000,'sample_rate':16000}
        return profile, analysis

    def test_reviewed_instrument_alignment_does_not_follow_bad_polyphonic_f0(self):
        profile, analysis = self._reviewed_instrument_profile()
        events, gains, report = build(profile,[{'frequency_hz':0,'duration_ms':2000}],analysis)
        self.assertEqual([e.duration_ms for e in events], [500,1000,500])
        self.assertEqual([e.frequency_hz for e in events], [0,440,0])
        self.assertEqual(report['sections'][0]['alignment_method'], 'reviewed_recording_alignment')
        self.assertNotIn('pitch_agreement_65_cents', report['sections'][0])

    def test_reviewed_alignment_cannot_exceed_recording_or_use_invalid_time(self):
        for offset, quarter in [(.5,.5),(2,.25),(-.1,.25),(.5,0),(.5,float('nan'))]:
            profile, analysis = self._reviewed_instrument_profile()
            profile['sections'][0]['reviewed_alignment'].update(offset_seconds=offset,quarter_seconds=quarter)
            with self.assertRaises(ValueError):
                build(profile,[{'frequency_hz':0,'duration_ms':2000}],analysis)


if __name__ == '__main__': unittest.main()
