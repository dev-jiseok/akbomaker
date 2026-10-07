import { afterEach, describe, expect, it, vi } from 'vitest';
import { request } from './api';

afterEach(() => vi.unstubAllGlobals());

describe('API failure status', () => {
  it('keeps HTTP 404 distinct from network failures for saved-draft recovery', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(JSON.stringify({ detail: '원본 작업이 없어요.' }), { status: 404 })));
    await expect(request('/api/score-omr/missing')).rejects.toMatchObject({ message: '원본 작업이 없어요.', status: 404 });
  });

  it('retains a safe fallback message and status when the response is not JSON', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response('Bad gateway', { status: 502 })));
    await expect(request('/api/score-omr/job')).rejects.toMatchObject({ message: '요청을 완료하지 못했어요. 잠시 뒤 다시 시도해주세요.', status: 502 });
  });

  it('does not label a connection failure as a deleted draft', async () => {
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new TypeError('Failed to fetch')));
    let error: unknown;
    try { await request('/api/score-omr/job'); } catch (caught) { error = caught; }
    expect(error).toBeInstanceOf(Error);
    expect(error).not.toHaveProperty('status');
    expect((error as Error).message).toContain('서버에 연결할 수 없어요');
  });
});
