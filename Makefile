FQBN := arduino:renesas_uno:unor4wifi
SKETCH := firmware/hid_injector

.PHONY: setup build
setup:
	arduino-cli core update-index
	arduino-cli core install arduino:renesas_uno@1.6.0
	arduino-cli lib install Keyboard@1.0.7 Mouse@1.0.1

build:
	python3 tools/patch_core.py
	arduino-cli compile --fqbn $(FQBN) --build-path build/hid_injector $(SKETCH)
