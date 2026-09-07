# Raspberry Pi 4B wearable camera enclosure kit

This folder contains a printable waist-mounted enclosure system for a Raspberry
Pi 4 Model B smart-glasses/camera project.

The system is split into two belt modules:

1. A ventilated Raspberry Pi 4B case with three guarded 12 mm buttons, a 30 mm
   fan mount and an upward camera-cable exit.
2. A slim belt cradle for the Infinix XP04 10,000 mAh power bank.

Keeping the power bank outside the Pi enclosure makes the wearable thinner,
keeps Li-polymer cells away from Pi heat and allows either part to be serviced
independently.

## Print-ready files

The `output` folder contains print-ready STL files:

- `pi4_wearable_base`
- `pi4_wearable_lid_3button_30mmfan`
- `infinix_xp04_belt_cradle`
- `xp04_fit_gauge`
- `button_12mm_fit_gauge`

STL files go directly to a slicer. `wearable_pi4_case.scad` is the fully
parametric OpenSCAD source, so hole sizes, clearances and enclosure dimensions
can be edited before exporting another STL.

## Important: print the gauges first

Retail listings consistently give the XP04 size as `135.5 x 67 x 15 mm`, but
physical products and printers vary.

1. Print `xp04_fit_gauge.stl` and slide it over the power bank.
2. Print `button_12mm_fit_gauge.stl`.
3. The button gauge holes are 12.2, 12.4 and 12.6 mm from left to right.
4. Adjust `XP04_CLEAR_*` or `BUTTON_HOLE_D` in the Python source if needed.

Do this before printing the full parts.

## Recommended hardware

- Raspberry Pi 4 Model B
- Four M2.5 x 8 mm screws for the Pi
- Four M3 x 10-12 mm self-tapping screws for the lid
- 30 x 30 x 7 mm 5 V fan with 24 mm mounting-hole pitch
- Four M3 fan screws and nuts
- Low-profile Pi 4 heatsink, about 6 mm high
- Three normally-open, 12 mm panel-mount momentary buttons
- Two 15-20 mm soft hook-and-loop straps for each belt module
- One 15 mm hook-and-loop retention strap for the power bank
- Soft cable tie or lacing cord for camera-cable strain relief

Do not use a rigid zip tie directly against a bare camera ribbon cable.

## Printing

- Material: PETG recommended
- Layer height: 0.20 mm
- Nozzle: 0.4 mm
- Walls/perimeters: 4
- Top/bottom layers: 5
- Infill: 25% gyroid or cubic
- Supports: build-plate-only around port openings if your printer needs them

Print the base and power-bank cradle with their broad rear surfaces on the bed.
Print the lid flat with its plain inside face on the bed and button guards
facing upward.

PLA can soften inside a hot car or in strong sun. PETG is preferable for a
wearable.

## Assembly

1. Thread two soft straps through each base's recessed slot pairs.
2. Secure the power bank in its open cradle with the third retention strap.
3. Fit heatsinks to the Pi.
4. Mount the Pi on the four 58 x 49 mm standoffs.
5. Mount the fan under the lid as an intake, blowing toward the heatsink.
6. Mount the three buttons in the guarded holes.
7. Route the camera cable through the rounded upper exit.
8. Anchor a protective cable sleeve to the two strain-relief slots.
9. Close the lid with four M3 self-tapping screws.

Keep the outward fan grille and upper exhaust slots uncovered by clothing.

## Suggested button wiring

Use the Pi's internal pull-ups. Connect each normally-open button between its
GPIO and ground:

| Function | BCM GPIO | Physical pin |
|---|---:|---:|
| Language | GPIO17 | 11 |
| Capture | GPIO27 | 13 |
| Pause/resume | GPIO22 | 15 |
| Common ground | GND | 6 |

A 1 kOhm series resistor in each GPIO lead provides extra protection. Never
apply 5 V to a GPIO input.

## Power warning

The Infinix XP04 is listed as `5 V / 2 A` per USB output. Raspberry Pi specifies
a `5 V / 3 A` supply for the Pi 4B. A camera, fan, CPU load and Wi-Fi can push
this project beyond the XP04's reliable output, causing undervoltage warnings,
reboots or SD-card corruption.

The supplied cradle fits the requested XP04, but use a verified power bank with
a stable 5 V / 3 A USB-C output for the final wearable if testing shows any
undervoltage.

## Camera-cable warning

A bare CSI ribbon from the waist to glasses is vulnerable to repeated bending
and pulling. For a wearable, a protected USB camera cable or a CSI-to-HDMI
extension is more durable. If a CSI ribbon is used:

- place it inside a soft braided sleeve;
- maintain at least a 10 mm bend radius;
- anchor the sleeve at the case and glasses;
- add clothing clips so the Pi connector carries no pulling force.

## Design basis

- Pi PCB: 85 x 56 mm
- Pi mounting pattern: 58 x 49 mm
- XP04: 135.5 x 67 x 15 mm
- Main case: approximately 122 x 76 x 37 mm including lid
- Power-bank cradle: approximately 142 x 75 x 20 mm
- Belt compatibility: up to approximately 50 mm, using adjustable straps

Measure real components before ordering a long final print.
