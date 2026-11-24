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

from chromascii import glyph_atlas, image_ascii, image_blocks

atlas, cw, ch = glyph_atlas(" @")
pic = image_ascii(img, atlas, bg=0.5)
assert pic.shape == (2 * ch, 2 * cw, 3), "one cell per pixel"
assert (pic[:ch, :cw] == 0).all(), "black pixel -> black cell"
white = pic[:ch, cw:2 * cw]
assert white.max() == 255 and white.min() == 127, "white cell: glyph at full color, background at half"
blk = image_blocks(img, cw, ch)
assert blk.shape == (2 * ch // 2, 2 * cw, 3) and (blk[ch // 2:, :cw] == [255, 0, 0]).all(), "bottom pixel fills bottom half"
print("export ok")
