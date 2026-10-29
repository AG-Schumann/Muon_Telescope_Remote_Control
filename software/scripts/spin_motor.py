#!/usr/bin/env python3
"""
scripts/spin_motor.py — basit motor testi (Stepper surucusu uzerinden)

Belirtilen aci kadar GORELI dondur, sonra dur. Amac: donanim zincirini
(STEP/DIR/EN + gearbox) ve Stepper surucusunu dogrulamak. Homing YOK;
sadece mevcut konumdan itibaren delta hareket.

    python scripts/spin_motor.py --deg 90            # +90 derece
    python scripts/spin_motor.py --deg -45           # ters yon
    python scripts/spin_motor.py --deg 360 --rate 400
    python scripts/spin_motor.py --deg 90 --hold     # hareket sonrasi enerjili kal

Not: 'software/' kokunden calistir (config yolu goreli).
Limit switch takiliysa hareket sirasinda otomatik guvenlik devrede olur.
"""
from __future__ import annotations

import argparse

from muon_control.config import load_config
from muon_control.drivers.stepper import Stepper, LimitError


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(description="Basit motor testi (Stepper surucusu)")
    ap.add_argument("--config", default="config/default.toml")
    ap.add_argument("--deg", type=float, required=True, help="donulecek aci (derece, +/-)")
    ap.add_argument("--rate", type=float, default=None,
                    help="step hizi (step/s); yoksa config'deki max_rate_hz")
    ap.add_argument("--hold", action="store_true",
                    help="hareket sonrasi motoru enerjili birak (konumu tut, cip isinir)")
    args = ap.parse_args(argv)

    cfg = load_config(args.config)
    # Surucuyu limitsiz kur: bu bir donanim smoke-test'i, homing gerekmiyor.
    # (Limit switch takiliysa ve guvenlik istersen: LimitSwitches.from_config(cfg) gecir.)
    st = Stepper.from_config(cfg, limits=None)
    if args.rate is not None:
        st.rate = args.rate

    # GORELI hedef: mevcut mutlak konum + delta. Surucu geri kalani halleder
    # (yon, aktif-low enable, adim basina konum guncelleme, kesirli->tam sayi).
    delta_steps = round(args.deg * st.steps_per_deg)
    target = st.position_steps + delta_steps

    print(f"[spin] aci={args.deg:+.1f}°  ->  {abs(delta_steps)} step  "
          f"(yon={'+' if delta_steps >= 0 else '-'}, {st.rate:.0f} step/s)")
    print(f"[spin] steps_per_deg={st.steps_per_deg:.4f}  "
          f"(microstep={cfg['stepper']['microstep']}, "
          f"{cfg['stepper']['gear_ratio']:.0f}:1 gear)")

    try:
        st.enable()
        st.move_to_steps(target)
        print(f"[spin] tamam: konum={st.position_steps} step "
              f"({st.angle_deg:+.3f}°)")
    except LimitError as e:
        print(f"[spin] limit: {e}  (durulan konum={st.position_steps} step)")
    except KeyboardInterrupt:
        print(f"\n[spin] Ctrl-C — durduruldu (konum={st.position_steps} step)")
    finally:
        if not args.hold:
            st.disable()      # bobinleri serbest birak (cip soger, tutma torku gider)


if __name__ == "__main__":
    main()