import numpy as np

from chromascii import render_ascii, render_blocks

img = np.array([[[0, 0, 0], [255, 255, 255]],
                [[255, 0, 0], [0, 0, 255]]], np.uint8)

a = render_ascii(img, " @")
assert "\x1b[38;2;0;0;0m " in a, "black pixel -> darkest char"
assert "\x1b[38;2;255;255;255m@" in a, "white pixel -> brightest char"
assert a.count("\n") == 1, "one line per pixel row"

b = render_blocks(img)
assert b.count("▀") == 2 and "\n" not in b, "two pixel rows fold into one text row"
assert "\x1b[38;2;0;0;0m\x1b[48;2;255;0;0m▀" in b, "top = text color, bottom = background"
print("ok")

c = render_ascii(img, " @", bg=0.5)
assert "\x1b[38;2;255;255;255m\x1b[48;2;127;127;127m@" in c, "background is the dimmed pixel color"
print("bg ok")
