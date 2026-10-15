'use client';
import { useEffect, useState } from 'react';
import type { Page, RunEvent } from '@/types/scholarflow';
import { scholarRequest } from './api';

export function useRunEvents(runId: string | undefined) {
  const [events, setEvents] = useState<RunEvent[]>([]);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    let active = true, cursor = 0, busy = false;
    const controller = new AbortController();
    setEvents([]);
    async function poll() {
      if (!runId || busy) return;
      busy = true;
      try {
        let more: string | null | undefined;
        do {
          const data = await scholarRequest<Page<RunEvent>>(`/runs/${runId}/events?after=${cursor}`, { signal: controller.signal });
          if (!active) return;
          for (const event of data.items) cursor = Math.max(cursor, event.seq);
          setEvents(previous => Array.from(new Map([...previous, ...data.items].map(event => [event.seq, event])).values()).sort((a, b) => a.seq - b.seq));
          more = data.next_cursor;
        } while (more && active);
        setError(null);
      } catch (failure) { if (active) setError('进度连接中断，正在补读已保存事件'); }
      finally { busy = false; }
    }
    void poll();
    const timer = setInterval(() => void poll(), 2000);
    return () => { active = false; controller.abort(); clearInterval(timer); };
  }, [runId]);
  return { events, error };
}
