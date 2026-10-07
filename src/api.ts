import type { Job } from './types';

async function readResponse<T>(response: Response): Promise<T> {
  const body = await response.json().catch(() => ({}));
  if (!response.ok) throw Object.assign(new Error(typeof body.detail === 'string' ? body.detail : '요청을 완료하지 못했어요. 잠시 뒤 다시 시도해주세요.'), { status: response.status });
  return body as T;
}

export async function request<T>(path: string, options?: RequestInit): Promise<T> {
  const deadline = AbortSignal.timeout(20_000);
  const signal = options?.signal ? AbortSignal.any([options.signal, deadline]) : deadline;
  try { return await readResponse<T>(await fetch(path, { ...options, signal })); }
  catch (error) {
    if (error instanceof DOMException && error.name === 'TimeoutError') throw new Error('서버 응답이 늦어지고 있어요. 잠시 뒤 다시 시도해주세요.');
    if (error instanceof TypeError) throw new Error('서버에 연결할 수 없어요. 음악 처리 서버가 실행 중인지 확인해주세요.');
    throw error;
  }
}

export function upload(data: FormData, onProgress: (value: number) => void, signal: AbortSignal): Promise<Job> {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open('POST', '/api/jobs');
    xhr.timeout = 120_000;
    const abort = () => xhr.abort();
    signal.addEventListener('abort', abort, { once: true });
    xhr.upload.onprogress = event => { if (event.lengthComputable) onProgress(Math.round(event.loaded / event.total * 100)); };
    xhr.onload = () => {
      signal.removeEventListener('abort', abort);
      try {
        const body = JSON.parse(xhr.responseText);
        if (xhr.status >= 200 && xhr.status < 300) resolve(body);
        else reject(new Error(typeof body.detail === 'string' ? body.detail : '업로드를 완료하지 못했어요.'));
      } catch { reject(new Error('서버 응답을 읽을 수 없어요.')); }
    };
    xhr.onerror = () => reject(new Error('서버에 연결할 수 없어요. 음악 처리 서버를 확인해주세요.'));
    xhr.ontimeout = () => reject(new Error('업로드 시간이 초과됐어요. 연결 상태를 확인해주세요.'));
    xhr.onabort = () => reject(new DOMException('업로드 취소', 'AbortError'));
    if (signal.aborted) abort();
    else xhr.send(data);
  });
}

const PROJECT_KEY = 'akbo-projects-v1';
export type RecentProject = Pick<Job, 'id' | 'title' | 'demo' | 'created_at' | 'status' | 'duration'>;
export function recentProjects(): RecentProject[] {
  try {
    const value: unknown = JSON.parse(localStorage.getItem(PROJECT_KEY) || '[]');
    if (!Array.isArray(value)) return [];
    return value.filter((item): item is RecentProject => !!item && typeof item === 'object' && /^[a-f0-9]{32}$/.test(item.id) && typeof item.title === 'string').slice(0, 20);
  } catch { return []; }
}
export function rememberProject(job: Job) {
  const { id, title, demo, created_at, status, duration } = job;
  const next = [{ id, title, demo, created_at, status, duration }, ...recentProjects().filter(project => project.id !== job.id)].slice(0, 20);
  try { localStorage.setItem(PROJECT_KEY, JSON.stringify(next)); } catch { /* Private browsing/storage limits must not interrupt processing. */ }
}
