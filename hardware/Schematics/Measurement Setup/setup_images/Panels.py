import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
from matplotlib.patches import FancyBboxPatch
import matplotlib.patheffects as pe

fig, ax = plt.subplots(figsize=(7, 11))
ax.set_aspect('equal')
ax.axis('off')
fig.patch.set_facecolor('white')

# ── Gerçek ölçüler (cm) ───────────────────────────────────────────────────────
OUTER_W   = 52.1
OUTER_H   = 112.7
INNER_W   = 43.9
FRAME_L   = 4.0
FRAME_BOT = 9.0

TOP_GAP   = 5.5
P_H       = 1.2     # tüm paneller aynı kalınlık
GAP_INNER = 8.2     # P1↔P2 ve P3↔P4 arası
MID_GAP   = 77.0    # P2↔P3 arası büyük boşluk
P3P4_GAP  = 8.2

# ── Panel y pozisyonları (alttan yukarı) ──────────────────────────────────────
P4_bot = FRAME_BOT
P4_top = P4_bot + P_H

P3_bot = P4_top + P3P4_GAP
P3_top = P3_bot + P_H

P2_bot = P3_top + MID_GAP       # ← büyük boşluk burada
P2_top = P2_bot + P_H

P1_bot = P2_top + GAP_INNER     # ← P1↔P2 küçük boşluk
P1_top = P1_bot + P_H

TOTAL_H = P1_top + TOP_GAP

# Panel x pozisyonları
P_X = FRAME_L
P_W = INNER_W

# ── Renkler ───────────────────────────────────────────────────────────────────
C_OUTER  = '#1a1a2e'
C_PANEL  = '#e74c3c'
C_PANEL_F= '#fdecea'
C_DIM    = '#555555'
FONT     = 'DejaVu Sans'

# ── Yardımcı: ölçü oku ────────────────────────────────────────────────────────
def dim_arrow(ax, x0, y0, x1, y1, label, horizontal=False, offset=0.8, fontsize=8):
    ax.annotate('', xy=(x1, y1), xytext=(x0, y0),
                arrowprops=dict(arrowstyle='<->', color=C_DIM, lw=1.0,
                                mutation_scale=8))
    mx, my = (x0+x1)/2, (y0+y1)/2
    if horizontal:
        ax.text(mx, my - offset, label, ha='center', va='top',
                fontsize=fontsize, color=C_DIM, fontname=FONT)
    else:
        ax.text(mx + offset, my, label, ha='left', va='center',
                fontsize=fontsize, color=C_DIM, fontname=FONT)

# ── Outer frame ───────────────────────────────────────────────────────────────
outer_x = (OUTER_W - INNER_W) / 2 - FRAME_L
ax.add_patch(plt.Rectangle((outer_x, 0), OUTER_W, TOTAL_H,
             linewidth=2.0, edgecolor=C_OUTER,
             facecolor='#f8f9fa', zorder=1))

# ── Inner frame (panelleri kapsayan alan) ─────────────────────────────────────
ax.add_patch(plt.Rectangle((P_X + outer_x, P4_bot),
             P_W, P1_top - P4_bot,
             linewidth=1.4, edgecolor='#16213e',
             facecolor='#f0f4ff', linestyle='--', zorder=2))

# ── Paneller ──────────────────────────────────────────────────────────────────
panels = [
    ('Panel 0', P1_bot),
    ('Panel 1', P2_bot),
    ('Panel 2', P3_bot),
    ('Panel 3', P4_bot),
]
for name, ybot in panels:
    ax.add_patch(plt.Rectangle((P_X + outer_x, ybot), P_W, P_H,
                 linewidth=1.5, edgecolor=C_PANEL,
                 facecolor=C_PANEL_F, zorder=3))
    ax.text(P_X + outer_x + P_W/2, ybot + P_H/2, name,
            ha='center', va='center', fontsize=9.5,
            color=C_PANEL, fontweight='bold', fontname=FONT, zorder=4)

# ── Ölçü okları (sağ) ────────────────────────────────────────────────────────
DX = P_X + outer_x + P_W + 1.5

dim_arrow(ax, DX, P1_top,  DX, P1_top + TOP_GAP, f'{TOP_GAP} cm',  offset=0.6)
dim_arrow(ax, DX, P1_bot,  DX, P1_top,            f'{P_H} cm',      offset=0.6)
dim_arrow(ax, DX, P2_top,  DX, P1_bot,            f'{GAP_INNER} cm',offset=0.6)
dim_arrow(ax, DX, P2_bot,  DX, P2_top,            f'{P_H} cm',      offset=0.6)
dim_arrow(ax, DX, P3_top,  DX, P2_bot,            f'{MID_GAP} cm',  offset=0.6)
dim_arrow(ax, DX, P3_bot,  DX, P3_top,            f'{P_H} cm',      offset=0.6)
dim_arrow(ax, DX, P4_top,  DX, P3_bot,            f'{P3P4_GAP} cm', offset=0.6)
dim_arrow(ax, DX, P4_bot,  DX, P4_top,            f'{P_H} cm',      offset=0.6)
dim_arrow(ax, DX, 0,       DX, P4_bot,            f'{FRAME_BOT} cm',offset=0.6)

# ── Yatay ölçüler (alt) ───────────────────────────────────────────────────────
dim_arrow(ax, outer_x, -3.5, outer_x + OUTER_W, -3.5,
          f'{OUTER_W} cm (outer)', horizontal=True, offset=1.2)
dim_arrow(ax, P_X + outer_x, -6.5, P_X + outer_x + INNER_W, -6.5,
          f'{INNER_W} cm (inner)', horizontal=True, offset=1.2)

# ── Sol: toplam yükseklik ─────────────────────────────────────────────────────
dim_arrow(ax, outer_x - 2.5, 0, outer_x - 2.5, TOTAL_H,
          f'{OUTER_H} cm', offset=0.6)

# ── Başlık ────────────────────────────────────────────────────────────────────
ax.text(outer_x + OUTER_W/2, TOTAL_H + 3,
        'Muon Telescope — Panel Layout',
        ha='center', va='bottom', fontsize=12,
        fontweight='bold', fontname=FONT, color=C_OUTER)
ax.text(outer_x + OUTER_W/2, TOTAL_H + 1.2,
        'All dimensions in cm',
        ha='center', va='bottom', fontsize=8.5,
        fontname=FONT, color=C_DIM)

ax.set_xlim(outer_x - 5, outer_x + OUTER_W + 8)
ax.set_ylim(-9, TOTAL_H + 6)

plt.tight_layout()
plt.savefig('./panel_layout_v2.pdf',
            dpi=300, bbox_inches='tight', facecolor='white')
plt.savefig('./panel_layout_v2.png',
            dpi=200, bbox_inches='tight', facecolor='white')
print(f"TOTAL_H = {TOTAL_H:.1f} cm (hedef: {OUTER_H} cm)")