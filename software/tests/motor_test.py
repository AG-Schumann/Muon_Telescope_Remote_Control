#Muon Telescope Motor Test Script


from gpiozero import Button
from signal import pause

PIN = 23 # We use GPIO23, but you can change this to the pin you want to test


sensor = Button(PIN, pull_up=False)

def on_rising():
    print("Event! (rising edge)")

sensor.when_pressed = on_rising   # automatically called on LOW->HIGH transition

print("Pin is waiting for events. Trigger an event from Signal Generator. Press Ctrl-C to exit.")
pause()