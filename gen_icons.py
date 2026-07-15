from PIL import Image, ImageDraw, ImageFont
import os

RES = r"app\src\main\res"
SIZES = {"mdpi":108, "hdpi":162, "xhdpi":216, "xxhdpi":324, "xxxhdpi":432}
FONT_PATH = r"C:\Windows\Fonts\msyhbd.ttc"  # 微软雅黑 Bold
if not os.path.exists(FONT_PATH):
    FONT_PATH = r"C:\Windows\Fonts\msyh.ttc"

def draw_yong(sz: int, bg=(220,38,38,255), fg=(255,255,255,255)) -> Image.Image:
    img = Image.new("RGBA", (sz, sz), (0,0,0,0))
    d = ImageDraw.Draw(img)
    # Adaptive icon safe zone: 中心 66% = 半径 sz*0.33 的圆内保证可见
    # 背景圆
    pad = int(sz*0.08)
    d.ellipse((pad,pad,sz-pad,sz-pad), fill=bg)
    # "勇" 字
    for pt in range(int(sz*0.70), 12, -2):
        try:
            font = ImageFont.truetype(FONT_PATH, pt)
            l,t,r,b = font.getbbox("勇")
            tw, th = r-l, b-t
            if tw <= sz*0.62 and th <= sz*0.62:
                break
        except Exception:
            continue
    l,t,r,b = font.getbbox("勇")
    tw, th = r-l, b-t
    x = (sz - tw)/2 - l
    y = (sz - th)/2 - t
    d.text((x, y), "勇", font=font, fill=fg)
    return img

# Android adaptive icon foreground (透明背景 + 居中"勇"图形)
def draw_fg(sz: int) -> Image.Image:
    img = Image.new("RGBA", (sz, sz), (0,0,0,0))
    d = ImageDraw.Draw(img)
    # foreground 中心 66% 区域
    inner = int(sz*0.60)
    off = (sz - inner)//2
    # 白色实心圆 + 红色勇字（也可全红勇字）
    d.ellipse((off,off,off+inner,off+inner), fill=(255,255,255,255))
    for pt in range(int(inner*0.70), 12, -2):
        try:
            font = ImageFont.truetype(FONT_PATH, pt)
            l,t,r,b = font.getbbox("勇")
            tw, th = r-l, b-t
            if tw <= inner*0.72 and th <= inner*0.72:
                break
        except Exception:
            continue
    l,t,r,b = font.getbbox("勇")
    tw, th = r-l, b-t
    x = (sz - tw)/2 - l
    y = (sz - th)/2 - t
    d.text((x, y), "勇", font=font, fill=(220,38,38,255))
    return img

# 老式方形 ic_launcher.png（用于低于 API 26 或作为兜底）
for dpi, size in SIZES.items():
    dst = os.path.join(RES, f"mipmap-{dpi}")
    os.makedirs(dst, exist_ok=True)
    draw_yong(size).save(os.path.join(dst, "ic_launcher.png"))
    # 圆形版
    im = draw_yong(size)
    # 已经是圆形，直接复用
    im.save(os.path.join(dst, "ic_launcher_round.png"))
    # foreground（adaptive）
    draw_fg(size).save(os.path.join(dst, "ic_launcher_foreground.png"))
    print(f"wrote {dpi} size={size}")

# PC 托盘图标 —— 存到 pc/assets/tray.png
os.makedirs("pc/assets", exist_ok=True)
draw_yong(128).save("pc/assets/tray_normal.png")
draw_yong(128, bg=(255,59,48,255)).save("pc/assets/tray_alert.png")
print("tray icons done")
