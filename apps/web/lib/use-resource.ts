"use client";

import { useCallback, useEffect, useState } from "react";

import { errorMessage } from "./api";

/* A small keyed cache so moving between tabs does not refetch published snapshot data. */
const cache = new Map<string, unknown>();

export type Resource<T> = {
  data: T | null;
  error: string | null;
  loading: boolean;
  reload: () => void;
};

export function useResource<T>(key: string | null, load: () => Promise<T>): Resource<T> {
  const [result, setResult] = useState<{ key: string; data: T | null; error: string | null } | null>(null);
  const [nonce, setNonce] = useState(0);

  useEffect(() => {
    if (!key || cache.has(key)) return;
    let active = true;
    load()
      .then((data) => {
        cache.set(key, data);
        if (active) setResult({ key, data, error: null });
      })
      .catch((caught: unknown) => {
        if (active) setResult({ key, data: null, error: errorMessage(caught, "Data could not be loaded.") });
      });
    return () => {
      active = false;
    };
    // `load` is identified by `key`; `nonce` forces a refetch after reload().
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key, nonce]);

  const reload = useCallback(() => {
    if (key) cache.delete(key);
    setNonce((value) => value + 1);
  }, [key]);

  const own = result && result.key === key ? result : null;
  const data = key ? ((cache.get(key) as T | undefined) ?? own?.data ?? null) : null;
  const error = data === null ? (own?.error ?? null) : null;
  return { data, error, loading: Boolean(key) && data === null && error === null, reload };
}

export function invalidate(prefix: string) {
  for (const key of cache.keys()) if (key.startsWith(prefix)) cache.delete(key);
}
