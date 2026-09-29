"use client";

import type { AnalysisState, Repository } from "@code-genome/contracts";
import { createContext, useCallback, useContext, useEffect, useMemo, useState } from "react";

import { api, errorMessage } from "../lib/api";

type Toast = { id: number; text: string };

type WorkspaceValue = {
  repositories: Repository[];
  states: Record<string, AnalysisState | undefined>;
  loading: boolean;
  error: string | null;
  reload: () => void;
  addRepository: (cloneUrl: string, branch: string) => Promise<Repository>;
  setRepositoryState: (repositoryId: string, state: AnalysisState | undefined) => void;
  dialog: "add-repository" | "palette" | null;
  setDialog: (dialog: "add-repository" | "palette" | null) => void;
  toast: (text: string) => void;
  toasts: Toast[];
  railOpen: boolean;
  setRailOpen: (open: boolean) => void;
};

const WorkspaceContext = createContext<WorkspaceValue | null>(null);

export function useWorkspace(): WorkspaceValue {
  const value = useContext(WorkspaceContext);
  if (!value) throw new Error("useWorkspace must be used inside WorkspaceProvider");
  return value;
}

export function WorkspaceProvider({ children }: { children: React.ReactNode }) {
  const [repositories, setRepositories] = useState<Repository[]>([]);
  const [states, setStates] = useState<Record<string, AnalysisState | undefined>>({});
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [nonce, setNonce] = useState(0);
  const [dialog, setDialog] = useState<WorkspaceValue["dialog"]>(null);
  const [toasts, setToasts] = useState<Toast[]>([]);
  const [railOpen, setRailOpen] = useState(false);

  useEffect(() => {
    let active = true;
    api
      .listRepositories()
      .then(async (items) => {
        if (!active) return;
        setRepositories(items);
        setError(null);
        const entries = await Promise.all(
          items.slice(0, 40).map(async (repository) => {
            try {
              const runs = await api.listAnalyses(repository.id);
              return [repository.id, runs[0]?.state] as const;
            } catch {
              return [repository.id, undefined] as const;
            }
          }),
        );
        if (active) setStates(Object.fromEntries(entries));
      })
      .catch((caught: unknown) => {
        if (active) setError(errorMessage(caught, "Repositories could not be loaded."));
      })
      .finally(() => {
        if (active) setLoading(false);
      });
    return () => {
      active = false;
    };
  }, [nonce]);

  const toast = useCallback((text: string) => {
    const id = Date.now() + Math.random();
    setToasts((current) => [...current, { id, text }]);
    window.setTimeout(() => setToasts((current) => current.filter((item) => item.id !== id)), 3600);
  }, []);

  const addRepository = useCallback(async (cloneUrl: string, branch: string) => {
    const repository = await api.createRepository(cloneUrl, branch);
    setRepositories((current) => [repository, ...current.filter((item) => item.id !== repository.id)]);
    return repository;
  }, []);

  const setRepositoryState = useCallback((repositoryId: string, state: AnalysisState | undefined) => {
    setStates((current) => (current[repositoryId] === state ? current : { ...current, [repositoryId]: state }));
  }, []);

  const value = useMemo<WorkspaceValue>(
    () => ({
      repositories,
      states,
      loading,
      error,
      reload: () => setNonce((n) => n + 1),
      addRepository,
      setRepositoryState,
      dialog,
      setDialog,
      toast,
      toasts,
      railOpen,
      setRailOpen,
    }),
    [repositories, states, loading, error, addRepository, setRepositoryState, dialog, toast, toasts, railOpen],
  );

  return <WorkspaceContext.Provider value={value}>{children}</WorkspaceContext.Provider>;
}
