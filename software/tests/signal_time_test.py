from gpiozero import Button
from signal import pause

import time

PIN = 23 # We use GPIO23


sensor = Button(PIN, pull_up=False)

def on_rising():
    print("Event! (rising edge)")
    print(f"Time: {time.strftime('%d-%b-%Y %H:%M:%S')}")

sensor.when_pressed = on_rising   # automatically called on LOW->HIGH transition





print("Pin is waiting for events. Trigger an event from Signal Generator. Press Ctrl-C to exit.")
pause()