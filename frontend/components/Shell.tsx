import Link from 'next/link';
const links=[['/','INDEX'],['/agents','AGENTS'],['/worlds','WORLDS'],['/scenarios','SCENARIOS'],['/suites','SUITES'],['/constraints','CONSTRAINTS'],['/runs','RUNS'],['/compare','DIFF'],['/lab','BREAK LAB'],['/enterprise','FABRIC']];
export function Shell({children}:{children:React.ReactNode}){
  return <div className="shell">
    <aside className="rail">
      <div className="brandLock"><div className="brandMark">O</div><div><div className="brand">ORDEAL</div><div className="brandSub">AUTONOMOUS SOFTWARE LAB</div></div></div>
      <div className="scope"><span>CONTROL</span><i></i><span>FABRIC</span></div>
      <nav className="nav">{links.map(([href,label],i)=><Link key={href} href={href}><b>{String(i+1).padStart(2,'0')}</b><span>{label}</span></Link>)}</nav>
      <div className="railFooter"><div className="pulseLine"><i></i><i></i><i></i><i></i><i></i></div><span>STATEFUL / REPLAYABLE</span></div>
    </aside>
    <main className="main"><div className="scanline"></div>{children}</main>
  </div>
}
