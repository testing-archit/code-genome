"use client";

import { use } from "react";

import { RepoProvider } from "../../../components/repo-context";

export default function RepositoryLayout({ children, params }: { children: React.ReactNode; params: Promise<{ id: string }> }) {
  const { id } = use(params);
  return <RepoProvider key={id} repositoryId={id}>{children}</RepoProvider>;
}
