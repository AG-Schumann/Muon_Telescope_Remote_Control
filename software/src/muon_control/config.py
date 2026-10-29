"""
muon_control.config — Layer 0: Configuration
 
TOML dosyasini yukler, turetilen buyuklukleri hesaplar, pin cakismasini yakalar.
 
    cfg = load_config("config/default.toml")
    cfg["stepper"]["steps_per_deg"]   # turetilen
    cfg["gpio"]["step"]               # pin erisimi
"""
import tomllib
 
 
def load_config(path="config/default.toml"):
    with open(path, "rb") as f:
        cfg = tomllib.load(f)
 
    # Turetilen: efektif cozunurluk (motor adimi x microstep x gear / 360)
    s = cfg["stepper"]
    s["steps_per_deg"] = s["steps_per_rev"] * s["microstep"] * s["gear_ratio"] / 360
 
    # Pin cakismasi (semadaki GPIO21/23 hatasi sinifi)
    # [gpio] altindaki bir deger ya tek bir pin ya da pin listesidir
    # (daq_signals gibi, kanal basina bir pin). Listeleri duzlestiriyoruz
    # ki asagidaki iki kontrol de HER pini gorsun -- duzlestirmezsek
    # set() "unhashable type: list" ile patlar, ve bir DAQ kanalinin
    # baska bir pinle cakismasi hic fark edilmezdi.
    pins = []
    for value in cfg["gpio"].values():
        if isinstance(value, list):
            pins.extend(value)
        else:
            pins.append(value)
    dupes = [p for p in set(pins) if pins.count(p) > 1]
    assert not dupes, f"pin cakismasi {dupes}: {cfg['gpio']}"
 
    # I2C (SDA=2/SCL=3) dijital IO'ya atanmasin — IMU hatti ile cakisir
    assert not (set(pins) & {2, 3}), f"I2C pini (2/3) dijital IO'ya atanmis: {cfg['gpio']}"
 
    return cfg
