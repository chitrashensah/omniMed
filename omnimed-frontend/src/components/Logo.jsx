import { useId } from 'react'

/**
 * OmniMed logo — a molecular hexagon (biomedical) whose six outer nodes
 * (the AI models) wire into a central consensus node. Uses the brand
 * coral → action-blue gradient. Scales cleanly from favicon to hero size.
 */
export default function Logo({ size = 28, className = '', title = 'OmniMed' }) {
  const uid = useId().replace(/:/g, '')
  const grad = `omnimed-grad-${uid}`
  const glow = `omnimed-glow-${uid}`

  // Hexagon vertices around center (16,16), radius 11, pointy-top.
  const V = [
    [16, 5],      // top
    [25.53, 10.5],// upper-right
    [25.53, 21.5],// lower-right
    [16, 27],     // bottom
    [6.47, 21.5], // lower-left
    [6.47, 10.5], // upper-left
  ]
  const hexPath = `M${V[0][0]} ${V[0][1]} L${V[1][0]} ${V[1][1]} L${V[2][0]} ${V[2][1]} L${V[3][0]} ${V[3][1]} L${V[4][0]} ${V[4][1]} L${V[5][0]} ${V[5][1]} Z`

  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 32 32"
      fill="none"
      xmlns="http://www.w3.org/2000/svg"
      className={className}
      role="img"
      aria-label={title}
      style={{ display: 'block', flexShrink: 0 }}
    >
      <title>{title}</title>
      <defs>
        <linearGradient id={grad} x1="5" y1="4" x2="27" y2="28" gradientUnits="userSpaceOnUse">
          <stop stopColor="#ff7759" />
          <stop offset="1" stopColor="#1863dc" />
        </linearGradient>
        <radialGradient id={glow} cx="0.5" cy="0.5" r="0.5">
          <stop stopColor="#ff7759" stopOpacity="0.35" />
          <stop offset="1" stopColor="#ff7759" stopOpacity="0" />
        </radialGradient>
      </defs>

      {/* soft glow behind the mark */}
      <circle cx="16" cy="16" r="13" fill={`url(#${glow})`} />

      {/* network edges: center → each model node */}
      <g stroke={`url(#${grad})`} strokeWidth="1.3" opacity="0.45" strokeLinecap="round">
        {V.map(([x, y], i) => (
          <line key={i} x1="16" y1="16" x2={x} y2={y} />
        ))}
      </g>

      {/* molecular hexagon */}
      <path
        d={hexPath}
        stroke={`url(#${grad})`}
        strokeWidth="2"
        strokeLinejoin="round"
      />

      {/* six model nodes */}
      {V.map(([x, y], i) => (
        <circle key={i} cx={x} cy={y} r="2.1" fill={`url(#${grad})`} />
      ))}

      {/* central consensus node */}
      <circle cx="16" cy="16" r="3.4" fill={`url(#${grad})`} />
      <circle cx="16" cy="16" r="1.5" fill="#fff" />
    </svg>
  )
}
