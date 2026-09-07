/*
  Parametric wearable enclosure kit

  - Raspberry Pi 4 Model B
  - three 12 mm panel-mount momentary buttons
  - 30 x 30 x 7 mm fan, 24 mm hole pitch
  - Infinix XP04 power bank, nominally 135.5 x 67 x 15 mm

  Select a part by changing `part`, or from the command line:
    openscad -D part=1 -o pi4_wearable_base.stl wearable_pi4_case.scad

  Part numbers:
    0 = assembly preview
    1 = Pi case base
    2 = Pi case lid
    3 = XP04 cradle
    4 = XP04 fit gauge
    5 = 12 mm button fit gauge

  Units: millimetres
*/

part = 0;
$fn = 32;

// Pi enclosure
case_l = 122;
case_w = 76;
case_h = 34;
wall = 2.4;
floor_t = 3;
corner_r = 5;
lid_t = 3;

// Pi 4B mounting geometry
pi_center_x = 13;
pi_center_y = 0;
pi_standoff_h = 5.5;
pi_standoff_d = 6.4;
pi_screw_pilot_d = 2.3;

// Three panel buttons
button_hole_d = 12.4;
button_guard_od = 19;
button_guard_id = 15;
button_guard_h = 1.2;

// Fan
fan_opening_d = 28;
fan_hole_pitch = 24;
fan_screw_d = 3.3;
fan_center_x = 13;
fan_center_y = 0;

// Infinix XP04 and fit allowances
xp04_l = 135.5;
xp04_w = 67;
xp04_h = 15;
xp04_clear_x = 2;
xp04_clear_y = 2;
xp04_clear_z = 2.2;
cradle_back = 3;
cradle_wall = 3;


module rounded_box(size=[10, 10, 10], r=2, center_xy=true) {
    x = size[0];
    y = size[1];
    z = size[2];
    translate(center_xy ? [0, 0, 0] : [x/2, y/2, 0])
        linear_extrude(height=z)
            offset(r=r)
                square([x - 2*r, y - 2*r], center=true);
}


module rounded_slot_2d(length=10, width=3) {
    hull() {
        translate([-(length-width)/2, 0]) circle(d=width);
        translate([(length-width)/2, 0]) circle(d=width);
    }
}


module rounded_slot_z(length=10, width=3, height=5, center_z=false) {
    linear_extrude(height=height, center=center_z)
        rounded_slot_2d(length, width);
}


module rounded_slot_through_y(length=20, width=4, depth=8) {
    // 2D slot lies in X/Z, extrusion passes through Y.
    rotate([90, 0, 0])
        linear_extrude(height=depth, center=true)
            rounded_slot_2d(length, width);
}


module pi_case_base() {
    lid_bosses = [
        [-55, -32], [-55, 32], [55, -32], [55, 32]
    ];

    // Official Pi 4B 58 x 49 mm hole pattern relative to board centre.
    pi_holes = [
        [-39, -24.5], [-39, 24.5], [19, -24.5], [19, 24.5]
    ];

    difference() {
        union() {
            // Rounded enclosure shell, open toward +Z.
            difference() {
                rounded_box([case_l, case_w, case_h], corner_r);
                translate([0, 0, floor_t])
                    rounded_box(
                        [case_l - 2*wall, case_w - 2*wall, case_h - floor_t + 2],
                        max(1, corner_r-wall)
                    );
            }

            // Lid screw bosses.
            for (p = lid_bosses)
                translate([p[0], p[1], floor_t])
                    cylinder(d=8, h=case_h-floor_t);

            // Raspberry Pi standoffs.
            for (p = pi_holes)
                translate([
                    pi_center_x + p[0],
                    pi_center_y + p[1],
                    floor_t
                ])
                    cylinder(d=pi_standoff_d, h=pi_standoff_h);
        }

        // Recessed waist-strap channels and through slots.
        for (sx = [-47, 47]) {
            translate([sx, -31, 2])
                cube([20.5, 4, 6], center=true);
            translate([sx, 31, 2])
                cube([20.5, 4, 6], center=true);
            translate([sx, 0, floor_t-0.45])
                cube([20.5, 58, 0.9], center=true);
        }

        // Lid screw pilot holes.
        for (p = lid_bosses)
            translate([p[0], p[1], floor_t-0.5])
                cylinder(d=2.7, h=case_h-floor_t+1.5);

        // Pi screw pilots.
        for (p = pi_holes)
            translate([
                pi_center_x + p[0],
                pi_center_y + p[1],
                floor_t-0.5
            ])
                cylinder(d=pi_screw_pilot_d, h=pi_standoff_h+1.5);

        // USB/Ethernet service opening.
        translate([case_l/2, 0, 18])
            cube([9, 57, 23], center=true);

        // USB-C, micro-HDMI 0, micro-HDMI 1 and 3.5 mm A/V.
        for (port = [
            [-23, 13],
            [-8, 13],
            [8.5, 13],
            [41, 11]
        ])
            translate([port[0], -case_w/2, 13])
                cube([port[1], 8, 13], center=true);

        // Upper exhausts; warm air exits away from clothing.
        for (ex = [-15, -2, 11, 24, 37, 50])
            translate([ex, case_w/2, 21.5])
                cube([9, 8, 4], center=true);

        // Rounded upward camera-ribbon exit (15-16 mm FFC with clearance).
        translate([39, case_w/2, 28.5])
            rounded_slot_through_y(20, 4.2, 8);

        // Two strain-relief lacing slots.
        translate([26.5, case_w/2, 28.5])
            cube([3.2, 8, 6], center=true);
        translate([51.5, case_w/2, 28.5])
            cube([3.2, 8, 6], center=true);
    }
}


module pi_case_lid() {
    lid_bosses = [
        [-55, -32], [-55, 32], [55, -32], [55, 32]
    ];
    button_positions = [
        [-47, -22], [-47, 0], [-47, 22]
    ];

    difference() {
        union() {
            difference() {
                rounded_box([case_l, case_w, lid_t], corner_r);

                // Lid fastener clearance holes.
                for (p = lid_bosses)
                    translate([p[0], p[1], -1])
                        cylinder(d=3.4, h=lid_t+2);

                // Button mounting holes.
                for (p = button_positions)
                    translate([p[0], p[1], -1])
                        cylinder(d=button_hole_d, h=lid_t+2);

                // Fan intake.
                translate([fan_center_x, fan_center_y, -1])
                    cylinder(d=fan_opening_d, h=lid_t+2);

                // 24 mm-square fan screw pattern.
                for (dx = [-fan_hole_pitch/2, fan_hole_pitch/2])
                    for (dy = [-fan_hole_pitch/2, fan_hole_pitch/2])
                        translate([
                            fan_center_x+dx,
                            fan_center_y+dy,
                            -1
                        ])
                            cylinder(d=fan_screw_d, h=lid_t+2);

                // External lacing slots near the camera exit.
                translate([32, 29, -1])
                    rounded_slot_z(8, 2.8, lid_t+2);
                translate([48, 29, -1])
                    rounded_slot_z(8, 2.8, lid_t+2);
            }

            // Low guard rings reduce accidental waist presses.
            for (p = button_positions)
                translate([p[0], p[1], lid_t])
                    difference() {
                        cylinder(d=button_guard_od, h=button_guard_h);
                        translate([0, 0, -0.1])
                            cylinder(d=button_guard_id, h=button_guard_h+0.2);
                    }

            // Integrated 3 x 3 fan finger grille.
            intersection() {
                translate([fan_center_x, fan_center_y, 0])
                    cylinder(d=fan_opening_d, h=lid_t);
                union() {
                    for (o = [-8, 0, 8]) {
                        translate([fan_center_x, fan_center_y+o, lid_t/2])
                            cube([fan_opening_d, 1.8, lid_t], center=true);
                        translate([fan_center_x+o, fan_center_y, lid_t/2])
                            cube([1.8, fan_opening_d, lid_t], center=true);
                    }
                }
            }
        }
    }
}


module xp04_cradle() {
    cavity_l = xp04_l + xp04_clear_x;
    cavity_w = xp04_w + xp04_clear_y;
    cavity_h = xp04_h + xp04_clear_z;

    outer_l = cavity_l + cradle_back + 1.5;
    outer_w = cavity_w + 2*cradle_wall;
    outer_h = cradle_back + cavity_h;

    cavity_start_x = -outer_l/2 + cradle_back;
    cavity_end_x = outer_l/2 + 2;
    cut_l = cavity_end_x - cavity_start_x;
    cut_center_x = (cavity_start_x + cavity_end_x)/2;
    retain_x = outer_l/2 - 22;

    difference() {
        rounded_box([outer_l, outer_w, outer_h], 4.5);

        // Open-top cavity extended through the full connector end (+X).
        translate([cut_center_x, 0, cradle_back])
            rounded_box([cut_l, cavity_w, cavity_h+2], 2);

        // Two recessed waist-strap channels.
        for (sx = [-42, 42]) {
            translate([sx, -31, 2])
                cube([22, 4, 6], center=true);
            translate([sx, 31, 2])
                cube([22, 4, 6], center=true);
            translate([sx, 0, cradle_back-0.45])
                cube([22, 58, 0.9], center=true);
        }

        // 15 mm power-bank retention strap.
        translate([retain_x, -31, 2])
            cube([17, 4, 6], center=true);
        translate([retain_x, 31, 2])
            cube([17, 4, 6], center=true);
        translate([retain_x, 0, cradle_back-0.45])
            cube([17, 58, 0.9], center=true);

        // Side lightening and ventilation windows.
        for (wx = [-42, -10, 22])
            for (wy = [-outer_w/2, outer_w/2])
                translate([wx, wy, 12])
                    cube([22, 8, 8], center=true);
    }
}


module xp04_fit_gauge() {
    cavity_w = xp04_w + xp04_clear_y;
    cavity_h = xp04_h + xp04_clear_z;
    outer_w = cavity_w + 2*cradle_wall;
    outer_h = cradle_back + cavity_h;

    difference() {
        rounded_box([20, outer_w, outer_h], 3);
        translate([0, 0, cradle_back])
            rounded_box([24, cavity_w, cavity_h+2], 1.5);
    }
}


module button_fit_gauge() {
    difference() {
        rounded_box([78, 28, 3], 3);
        for (hole = [
            [-25, 12.2],
            [0, 12.4],
            [25, 12.6]
        ])
            translate([hole[0], 0, -1])
                cylinder(d=hole[1], h=5);
    }
}


module assembly_preview() {
    color([0.18, 0.55, 0.82])
        pi_case_base();
    color([0.9, 0.45, 0.12])
        translate([0, 0, case_h+8])
            pi_case_lid();
    color([0.25, 0.72, 0.42])
        translate([0, -105, 0])
            xp04_cradle();
}


if (part == 1)
    pi_case_base();
else if (part == 2)
    pi_case_lid();
else if (part == 3)
    xp04_cradle();
else if (part == 4)
    xp04_fit_gauge();
else if (part == 5)
    button_fit_gauge();
else
    assembly_preview();
