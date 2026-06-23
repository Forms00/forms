from PIL import Image

img = Image.open("signature.png").convert("RGBA")

new_data = []
for item in img.getdata():
    # Make near-white pixels transparent
    if item[0] > 240 and item[1] > 240 and item[2] > 240:
        new_data.append((255, 255, 255, 0))
    else:
        new_data.append(item)

img.putdata(new_data)
img.save("signature_transparent.png")