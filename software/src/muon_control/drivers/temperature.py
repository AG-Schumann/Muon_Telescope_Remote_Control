"""
muon_control.drivers.temperature — Layer 1: temperature/humidity sensor (DHT11/DHT22)

Dependency injection: not tied to the concrete adafruit_dht; any 'sensor'
object that provides .temperature and .humidity properties is accepted
(tested with a mock).

    temp = Temperature.from_config(cfg)
    t, rh = temp.read()          # (°C, %RH), or (None, None) if the read fails
    t = temp.read_temperature()  # °C only (may be None)
"""

from __future__ import annotations


class Temperature:
    def __init__(self, sensor):
        """sensor: object with .temperature (°C) and .humidity (%RH)
        properties (an adafruit_dht.DHT11/DHT22 instance). Raises
        RuntimeError on a failed read."""
        self._sensor = sensor

    @classmethod
    def from_config(cls, cfg: dict, sensor=None) -> "Temperature":
        s = cfg["temperature"]
        pin = cfg["gpio"]["temp"]
        if sensor is None:
            import board                      
            import adafruit_dht
            pin_obj = getattr(board, f"D{pin}")           
            sensor_cls = getattr(adafruit_dht, s["sensor_type"])  # "DHT11" -> adafruit_dht.DHT11
            # use_pulseio=False: yeni Pi/kernel'de daha guvenilir okuma
            sensor = sensor_cls(pin_obj, use_pulseio=False)
        return cls(sensor)

    # --- okuma -------------------------------------------------------------
    def read(self) -> tuple[float | None, float | None]:
        """(temperature_C, humidity_RH), or (None, None) if the read fails.
        The newer library raises RuntimeError on a failed read; it is
        caught here."""
        try:
            t = self._sensor.temperature
            rh = self._sensor.humidity
            return t, rh
        except RuntimeError:
            # DHT protokol zamanlama kaymasi: normal, tekrar denenebilir
            return None, None

    def read_temperature(self) -> float | None:
        """Sadece sicaklik (°C), veya okuma basarisizsa None."""
        t, _ = self.read()
        return t