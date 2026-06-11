from picamera2 import Picamera2
from picamera2.previews import QtGlPreview
import time

cam0 = Picamera2(0)
cam1 = Picamera2(1)

config0 = cam0.create_preview_configuration(main={"size": (1280, 720)})
config1 = cam1.create_preview_configuration(main={"size": (1280, 720)})

cam0.configure(config0)
cam1.configure(config1)

cam0.start_preview(QtGlPreview())
cam1.start_preview(QtGlPreview())

cam0.start()
cam1.start()

try:
    while True:
        time.sleep(1)
except KeyboardInterrupt:
    pass

cam0.stop()
cam1.stop()