import { NextRequest, NextResponse } from 'next/server';

export const dynamic = 'force-dynamic';
export const runtime = 'nodejs';
const id = '[A-Za-z0-9_-]+';
const routes = new RegExp(`^(?:status|projects(?:/${id}(?:/(?:plans(?:/${id}/approve)?|papers(?:/${id})?|selections|runs|imports|extractions(?:/${id})?|reports|reading-status|reading-retries))?)?|papers/${id}/versions|documents/${id}(?:/(?:file|passages))?|runs/${id}(?:/(?:jobs|actions|events))?|claims/${id}/evidence|evidence/${id}/reviews|reports/${id}(?:/(?:checks|exports|citation-options))?|exports/${id}/file|imports/${id})$`);
async function proxy(request: NextRequest, { params }: { params: { path: string[] } }) {
  const path = params.path.join('/');
  if (!routes.test(path)) return NextResponse.json({ error: { code: 'invalid_path', message: '未知科研接口' } }, { status: 404 });
  const base = (process.env.SCHOLARFLOW_API_URL || 'http://127.0.0.1:8000').replace(/\/$/, '');
  const headers = new Headers();
  for (const name of ['content-type', 'range', 'if-range', 'idempotency-key', 'accept']) {
    const value = request.headers.get(name); if (value) headers.set(name, value);
  }
  try {
    const upstream = await fetch(`${base}/api/v1/${path}${request.nextUrl.search}`, {
      method: request.method, headers, cache: 'no-store', redirect: 'manual',
      body: ['GET', 'HEAD'].includes(request.method) ? undefined : request.body,
      // Node's fetch requires duplex for streamed multipart request bodies.
      ...({ duplex: 'half' } as RequestInit), signal: request.signal,
    });
    const output = new Headers({ 'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff' });
    for (const name of ['content-type', 'content-disposition', 'content-length', 'content-range', 'accept-ranges', 'etag']) {
      const value = upstream.headers.get(name); if (value) output.set(name, value);
    }
    return new NextResponse(upstream.body, { status: upstream.status, headers: output });
  } catch {
    return NextResponse.json({ error: { code: 'backend_unavailable', message: '无法连接科研服务，请检查服务是否已启动', retryable: true } }, { status: 502 });
  }
}
export { proxy as GET, proxy as POST, proxy as PATCH, proxy as HEAD };
