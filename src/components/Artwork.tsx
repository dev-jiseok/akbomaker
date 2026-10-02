export function StudioArtwork() {
  return <div className="studio-art" aria-hidden="true">
    <div className="art-grid" />
    <div className="vinyl"><div className="vinyl-label"><span>akbo</span><small>SIDE A · YOUR MUSIC</small><i /></div></div>
    <div className="art-sheet"><div className="sheet-heading"><span>Sunday, softly</span><small>Piano · 108 BPM</small></div><SheetDrawing /><div className="sheet-bottom">a little more you.<span>♮</span></div></div>
    <div className="art-wave"><span><i /> SEPARATE & CREATE</span><svg viewBox="0 0 230 36">{Array.from({ length: 46 }, (_, i) => { const h = 3 + Math.abs(Math.sin(i * 1.79) * Math.cos(i * 0.19)) * 28; return <rect key={i} x={i * 5} y={(36 - h) / 2} width="2.5" height={h} rx="1.25" />; })}</svg></div>
    <div className="art-star">✳</div><span className="art-caption">A space for the way you hear.</span>
  </div>;
}

export function SheetDrawing() {
  return <svg viewBox="0 0 280 115" className="sheet-drawing">
    {[0, 1].map(row => <g key={row} transform={`translate(0 ${row * 57})`}>
      {[0, 1, 2, 3, 4].map(i => <line key={i} x1="8" x2="272" y1={14 + i * 6} y2={14 + i * 6} stroke="#8e927d" strokeWidth="0.6" />)}
      <text x="12" y="39" fontSize="39" fontFamily="serif" fill="#424633">𝄞</text>
      <line x1="148" x2="148" y1="14" y2="38" stroke="#424633" strokeWidth="0.8" />
      <line x1="270" x2="270" y1="14" y2="38" stroke="#424633" strokeWidth="0.8" />
      {[55, 79, 104, 127, 170, 194, 219, 244].map((x, i) => { const y = 27 + [6, 0, -6, 0, 3, -3, -6, 0][(i + row) % 8]; return <g key={x}><ellipse cx={x} cy={y} rx="4.8" ry="3.4" transform={`rotate(-20 ${x} ${y})`} fill="#424633" /><line x1={x + 4} x2={x + 4} y1={y} y2={y - 21} stroke="#424633" strokeWidth="1.3" />{i % 2 === 0 && <path d={`M${x + 4} ${y - 21}l28 -3v3l-28 3z`} fill="#424633" />}</g>; })}
    </g>)}
  </svg>;
}
