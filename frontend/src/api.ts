const API_BASE = import.meta.env.VITE_API_URL ?? (import.meta.env.DEV ? 'http://localhost:8000' : '');

async function errorMessage(response: Response): Promise<string> {
  const text = await response.text();
  const contentType = response.headers.get('content-type') ?? '';
  if (contentType.includes('application/json')) {
    try {
      const data = JSON.parse(text);
      const detail = data?.detail ?? data?.error ?? data?.message;
      if (detail) return `HTTP ${response.status}: ${String(detail)}`;
    } catch { /* ignore */ }
  }
  const trimmed = text.trim();
  if (/^<!doctype html/i.test(trimmed) || /^<html/i.test(trimmed)) {
    return `HTTP ${response.status}: hosting proxy/server temporarily unavailable. Durable Talos jobs will resume when the service is available.`;
  }
  return `HTTP ${response.status}: ${trimmed.slice(0, 500) || response.statusText}`;
}

async function request<T>(path:string, init:RequestInit={}):Promise<T>{
  const response=await fetch(`${API_BASE}${path}`,{cache:'no-store',credentials:'same-origin',...init});
  if(!response.ok) throw new Error(await errorMessage(response));
  return response.json() as Promise<T>;
}
export const apiGet=<T>(path:string)=>request<T>(path);
export const apiPost=<T>(path:string,body:unknown)=>request<T>(path,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
export const apiPut=<T>(path:string,body:unknown)=>request<T>(path,{method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
export const apiDelete=<T>(path:string)=>request<T>(path,{method:'DELETE'});
export async function apiUpload<T>(path:string,file:File):Promise<T>{
  const form=new FormData();form.append('file',file);
  return request<T>(path,{method:'POST',body:form});
}
