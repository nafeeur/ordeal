import './globals.css';import {Shell} from '../components/Shell';
export const metadata={title:'Ordeal — Stateful Agent Testing',description:'Open-source stateful simulation, evaluation and replay for AI agents'};
export default function RootLayout({children}:{children:React.ReactNode}){return <html lang="en"><body><Shell>{children}</Shell></body></html>}
