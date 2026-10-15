import type { Metadata } from 'next';
import Link from 'next/link';
import './scholarflow.css';
import ModeBanner from '@/components/scholarflow/ModeBanner';

export const metadata: Metadata = { title: 'ScholarFlow · 科研文献工作台', description: '有原文依据、可复核的多智能体文献调研工作台' };
export default function ProjectsLayout({ children }: { children: React.ReactNode }) {
  return <main className="sf-shell" lang="zh-CN"><header className="sf-header"><Link href="/projects" className="sf-brand"><span aria-hidden="true">S</span> ScholarFlow</Link>
    <span className="sf-header-note">从文献到证据，再到研究报告</span><Link href="/">通用研究入口 ↗</Link></header><ModeBanner />{children}</main>;
}
