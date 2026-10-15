'use client';
import { useEffect, useState } from 'react';
import { scholarRequest } from '@/lib/scholarflow/api';
export default function ModeBanner() {
  const [demo, setDemo] = useState(false);
  useEffect(() => { scholarRequest<{ demo: boolean }>('/status').then(status => setDemo(status.demo)).catch(() => {}); }, []);
  return demo ? <div className="sf-notice" style={{ margin: '16px 4% 0' }}><strong>演示模式 · 合成文献与固定模型响应</strong><p className="sf-small">用于体验完整流程，不代表真实文献检索或科研评估；不会调用外部模型。</p></div> : null;
}
