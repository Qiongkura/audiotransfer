"""Run portable player checks as Cortex-M3 machine code in Unicorn.

Install optional tools: pip install unicorn pyelftools
Use the ARM compiler already installed by PlatformIO (or pass --compiler).
"""

from pathlib import Path
import argparse
import shutil
import subprocess
import tempfile
from elftools.elf.elffile import ELFFile
from unicorn import Uc, UC_ARCH_ARM, UC_MODE_THUMB, UC_MODE_MCLASS, UC_HOOK_CODE
from unicorn.arm_const import UC_ARM_REG_SP, UC_ARM_REG_LR


def main():
    root = Path(__file__).resolve().parent.parent
    default = shutil.which("arm-none-eabi-g++") or str(
        Path.home()
        / ".platformio/packages/toolchain-gccarmnoneeabi/bin/arm-none-eabi-g++.exe"
    )
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--compiler", default=default)
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="player-logic-") as directory:
        work = Path(directory)
        source, binary = work / "checks.cpp", work / "checks.elf"
        source.write_text(
            '#include "player_logic_checks.h"\nextern "C" void run_checks() {runPlayerLogicChecks();}\n',
            encoding="utf-8",
        )
        subprocess.run(
            [
                args.compiler,
                "-mcpu=cortex-m3",
                "-mthumb",
                "-Os",
                "-ffreestanding",
                "-nostdlib",
                "-std=c++11",
                "-I" + str(root / "tests"),
                "-I" + str(root / "audiotransfer"),
                "-Wl,-Ttext=0x08000000",
                "-Wl,--section-start=.bss=0x20000000",
                "-Wl,-e,run_checks",
                str(source),
                "-o",
                str(binary),
                "-lgcc",
            ],
            check=True,
        )
        emu = Uc(UC_ARCH_ARM, UC_MODE_THUMB | UC_MODE_MCLASS)
        emu.mem_map(0x08000000, 0x20000)
        emu.mem_map(0x20000000, 0x20000)
        with binary.open("rb") as stream:
            elf = ELFFile(stream)
            symbols = {
                s.name: s.entry.st_value
                for s in elf.get_section_by_name(".symtab").iter_symbols()
            }
            for segment in elf.iter_segments():
                if segment["p_type"] == "PT_LOAD":
                    emu.mem_write(segment["p_vaddr"], segment.data())
        sentinel = 0x0801FF00
        emu.reg_write(UC_ARM_REG_SP, 0x2001FFF0)
        emu.reg_write(UC_ARM_REG_LR, sentinel | 1)
        emu.hook_add(
            UC_HOOK_CODE,
            lambda uc, addr, size, _: uc.emu_stop() if addr == sentinel else None,
        )
        emu.emu_start(symbols["run_checks"] | 1, sentinel, count=1000000)
        read = lambda name: int.from_bytes(emu.mem_read(symbols[name], 4), "little")
        passed, failed = read("playerSelfTestPassed"), read("playerSelfTestFailures")
        print(f"Cortex-M3 logic checks: {passed} passed, {failed} failed")
        if failed or passed != 52:
            raise SystemExit(1)


if __name__ == "__main__":
    main()
