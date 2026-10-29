import matplotlib.pyplot as plt
import matplotlib.patches as mpatches

# ── Konfigürasyonlar ──────────────────────────────────────────────────────────
# Her konfigürasyon: (dosya_adı, başlık_alt, active panel listesi)
CONFIGS = [
    ('all_active',    '4-Fold Coincidence (P0∧P1∧P2∧P3)', [0, 1, 2, 3]),
    ('p03_active',    '2-Fold Coincidence (P0∧P3)',         [0, 3]),
    ('p12_active',    '2-Fold Coincidence (P1∧P2)',         [1, 2]),
    ('p03_p12',       '(P0∧P3) and (P1∧P2)',               [0, 1, 2, 3]),  # hepsi active ama gruplar farklı gösterilebilir
]

# ── Ölçüler (cm) ─────────────────────────────────────────────────────────────
OUTER_W   = 52.1
INNER_W   = 43.9
FRAME_L   = 4.0
FRAME_BOT = 9.0
TOP_GAP   = 5.5
P_H       = 1.2
GAP_INNER = 8.2
MID_GAP   = 27.0
P3P4_GAP  = 8.2

P4_bot = FRAME_BOT;          P4_top = P4_bot + P_H
P3_bot = P4_top + P3P4_GAP;  P3_top = P3_bot + P_H
P2_bot = P3_top + MID_GAP;   P2_top = P2_bot + P_H
P1_bot = P2_top + GAP_INNER;  P1_top = P1_bot + P_H
TOTAL_H = P1_top + TOP_GAP

outer_x = (OUTER_W - INNER_W) / 2 - FRAME_L
P_X = FRAME_L
P_W = INNER_W

# Panel sırası: index 0 = Panel 0 (en üst = P1_bot), index 3 = Panel 3 (en alt = P4_bot)
PANEL_YBOTS = [P1_bot, P2_bot, P3_bot, P4_bot]

# ── Renkler ───────────────────────────────────────────────────────────────────
C_OUTER     = '#1a1a2e'
C_FONT      = 'DejaVu Sans'
C_DIM       = '#888888'

# Active
C_ACT_EDGE  = '#c0392b'
C_ACT_FACE  = '#fdecea'
C_ACT_TEXT  = '#c0392b'
C_ACT_LABEL = '#c0392b'

# Inactive
C_INA_EDGE  = '#bbbbbb'
C_INA_FACE  = '#f0f0f0'
C_INA_TEXT  = '#aaaaaa'
C_INA_LABEL = '#bbbbbb'

# Inner frame
C_INNER_EDGE = '#16213e'
C_INNER_FACE = '#f0f4ff'


def draw_config(active_panels, subtitle, filename):
    fig, ax = plt.subplots(figsize=(7, 8))
    ax.set_aspect('equal')
    ax.axis('off')
    fig.patch.set_facecolor('white')

    # Outer frame
    ax.add_patch(plt.Rectangle((outer_x, 0), OUTER_W, TOTAL_H,
                 linewidth=2.0, edgecolor=C_OUTER,
                 facecolor='#f8f9fa', zorder=1))

    # Inner frame
    ax.add_patch(plt.Rectangle((P_X + outer_x, P4_bot),
                 P_W, P1_top - P4_bot,
                 linewidth=1.2, edgecolor=C_INNER_EDGE,
                 facecolor=C_INNER_FACE, linestyle='--', zorder=2))

    # Paneller
    for i, ybot in enumerate(PANEL_YBOTS):
        active = i in active_panels
        edge  = C_ACT_EDGE  if active else C_INA_EDGE
        face  = C_ACT_FACE  if active else C_INA_FACE
        tcol  = C_ACT_TEXT  if active else C_INA_TEXT
        lcol  = C_ACT_LABEL if active else C_INA_LABEL
        lw    = 1.6 if active else 0.9

        ax.add_patch(plt.Rectangle((P_X + outer_x, ybot), P_W, P_H,
                     linewidth=lw, edgecolor=edge,
                     facecolor=face, zorder=3))

        # Panel ismi (sol)
        ax.text(P_X + outer_x + 1.0, ybot + P_H/2,
                f'Panel {i}',
                ha='left', va='center', fontsize=9.5,
                color=tcol, fontweight='bold', fontname=C_FONT, zorder=4)

        # ACTIVE / INACTIVE etiketi (sağ)
        status = 'ACTIVE' if active else 'inactive'
        ax.text(P_X + outer_x + P_W - 1.0, ybot + P_H/2,
                status,
                ha='right', va='center', fontsize=8.5,
                color=lcol,
                fontweight='bold' if active else 'normal',
                fontname=C_FONT, zorder=4,
                fontstyle='normal' if active else 'italic')

    # Başlık
    ax.text(outer_x + OUTER_W/2, TOTAL_H + 3.0,
            'Muon Telescope — Panel Orientation',
            ha='center', va='bottom', fontsize=11,
            fontweight='bold', fontname=C_FONT, color=C_OUTER)
    ax.text(outer_x + OUTER_W/2, TOTAL_H + 1.3,
            subtitle,
            ha='center', va='bottom', fontsize=9,
            fontname=C_FONT, color='#333333')

    ax.set_xlim(outer_x - 2, outer_x + OUTER_W + 3)
    ax.set_ylim(-4, TOTAL_H + 6)

    plt.tight_layout()
    for ext in ['pdf', 'png']:
        plt.savefig(f'./panel_orient_{filename}.{ext}',
                    dpi=200, bbox_inches='tight', facecolor='white')
    plt.close()
    print(f"✓ {filename}")


# ── Üret ──────────────────────────────────────────────────────────────────────
draw_config([0, 1, 2, 3], '4-Fold Coincidence: P0 ∧ P1 ∧ P2 ∧ P3', 'all_active')
draw_config([0, 3],        '2-Fold Coincidence: P0 ∧ P3',             'p03')
draw_config([1, 2],        '2-Fold Coincidence: P1 ∧ P2',             'p12')
draw_config([0, 1, 2, 3], 'P0∧P3  and  P1∧P2  independently',        'p03_and_p12')