"use client";

import type { DefectChampion, MlOverviewWithInstability } from "@code-genome/contracts";
import Link from "next/link";

import {
  baselineSeries,
  CalibrationPlot,
  challengerSeries,
  ConfusionMatrix,
  DivergingBars,
  MetricBars,
  modelSeries,
  RankedBars,
} from "../../../../components/charts";
import { ModelsIcon } from "../../../../components/icons";
import { EvidenceChips, RequiresSnapshot, useRepo } from "../../../../components/repo-context";
import { Empty, Loading, Notice, Panel } from "../../../../components/ui";
import { formatDate, relativeTime, shortSha } from "../../../../lib/format";

export default function ModelsPage() {
  return (
    <RequiresSnapshot what="Trained models">
      <ModelsView />
    </RequiresSnapshot>
  );
}

function Abstained({ reason }: { reason: string | null | undefined }) {
  return <Notice tone="warn" title="Not enough history to train honestly">{reason ?? "The model abstained."} The rest of the app falls back to the transparent baseline.</Notice>;
}

function ModelsView() {
  const { models, trainModels, training, published } = useRepo();

  if (models.error) return <Notice tone="error" title="Models could not be loaded">{models.error}</Notice>;
  if (!models.data) return <Panel><Loading rows={5} /></Panel>;

  const overview: MlOverviewWithInstability = models.data;
  const trainedAt = Object.values(overview.tasks)[0]?.trained_at;

  return (
    <>
      <Panel
        title="Models trained on this repository"
        description={
          overview.trained
            ? <>Trained {relativeTime(trainedAt)} on snapshot <code>{shortSha(published?.snapshot_sha, 10)}</code>. Each model is evaluated on data it did not see: a later time period or cross-validation folds.</>
            : "Train seven models from this repository's own commits, files, and import graph. It takes a few seconds."
        }
        actions={
          <button className={`button ${overview.trained ? "button-secondary" : "button-primary"}`} disabled={training} onClick={() => void trainModels()} type="button">
            <ModelsIcon size={16} />{training ? "Training…" : overview.trained ? "Retrain" : "Train models"}
          </button>
        }
      >
        <p className="small muted" style={{ maxWidth: "80ch" }}>
          These are scikit-learn and NetworkX models fitted per repository. They power the risk ranking, impact predictions, search, chat and voice retrieval, and the commit labels in History. Gemini never trains or scores anything here; it only phrases answers from what these models retrieve.
        </p>
      </Panel>

      {!overview.trained ? (
        <Panel><Empty centered icon={<ModelsIcon size={34} />} title="No models yet">Models train automatically after each analysis. This snapshot was published before that, so train them now.</Empty></Panel>
      ) : (
        <>
          <SummaryCards overview={overview} />
          <DefectSection overview={overview} />
          <InstabilitySection overview={overview} />
          <IntentSection overview={overview} />
          <div className="grid-2">
            <ImpactSection overview={overview} />
            <RetrievalSection overview={overview} />
          </div>
          <div className="grid-2">
            <ModulesSection overview={overview} />
            <AnomalySection overview={overview} />
          </div>
          <div className="limitations">{overview.limitations.map((item) => <span key={item}>{item}</span>)}</div>
        </>
      )}
    </>
  );
}

function SummaryCards({ overview }: { overview: MlOverviewWithInstability }) {
  const { defect_risk: defect, commit_intent: intent, change_impact: link, retrieval, modules, anomalies, instability } = overview.tasks;
  const instabilityScores = instability?.result.metrics.model;
  const champion = defect?.result.champion;
  const championScores = champion ? defect?.result.metrics[champion] : undefined;
  const cards = [
    {
      title: "Defect-proneness",
      algo: championName(champion, true),
      trained: defect?.status === "trained",
      headline: championScores ? championScores.roc_auc.toFixed(2) : "—",
      unit: "ROC-AUC on the latest period",
      versus: defect?.result.metrics.heuristic_baseline ? `Heuristic baseline ${defect.result.metrics.heuristic_baseline.roc_auc.toFixed(2)}` : defect?.result.reason,
    },
    {
      title: "Commit intent",
      algo: "TF-IDF + logistic regression",
      trained: intent?.status === "trained",
      headline: intent ? intent.result.metrics.cv_macro_f1.toFixed(2) : "—",
      unit: "macro-F1, cross-validated",
      versus: intent ? `Majority class ${intent.result.metrics.majority_baseline_macro_f1.toFixed(2)}` : undefined,
    },
    {
      title: "Change impact",
      algo: "Link prediction, logistic regression",
      trained: link?.status === "trained",
      headline: link?.result.metrics.model ? link.result.metrics.model.roc_auc.toFixed(2) : "—",
      unit: "ROC-AUC on held-out files",
      versus: link?.result.metrics.baseline_past_cochange ? `Past co-change alone ${link.result.metrics.baseline_past_cochange.roc_auc.toFixed(2)}` : link?.result.reason,
    },
    {
      title: "Semantic retrieval",
      algo: `BM25 + LSA + rank fusion, serving ${retrieval?.result.metrics.selected_mode ?? "hybrid"}`,
      trained: retrieval?.status === "trained",
      headline: retrieval?.result.metrics.hybrid ? retrieval.result.metrics.hybrid.recall_at_10.toFixed(2) : "—",
      unit: "hybrid recall@10, commit messages as queries",
      versus: retrieval?.result.metrics.bm25 ? `BM25 alone ${retrieval.result.metrics.bm25.recall_at_10.toFixed(2)}, random ${(retrieval.result.metrics.random_baseline_recall ?? 0).toFixed(2)}` : retrieval?.result.reason,
    },
    {
      title: "Module discovery",
      algo: "Louvain community detection",
      trained: modules?.status === "trained",
      headline: modules?.result.metrics.modularity_learned?.toFixed(2) ?? "—",
      unit: "modularity",
      versus: modules?.result.metrics.modularity_directory_baseline !== undefined ? `Directory grouping ${modules.result.metrics.modularity_directory_baseline.toFixed(2)}` : modules?.result.reason,
    },
    {
      title: "Unusual commits",
      algo: "Isolation forest",
      trained: anomalies?.status === "trained",
      headline: anomalies?.result.metrics.flagged !== undefined ? String(anomalies.result.metrics.flagged) : "—",
      unit: `flagged of ${anomalies?.result.metrics.commits ?? "—"} commits`,
      versus: anomalies?.status === "trained" ? "Unusual is a prompt to review, not a finding" : anomalies?.result.reason,
    },
    {
      title: "Component instability",
      algo: "Windowed sequence model, logistic regression",
      trained: instability?.status === "trained",
      headline: instabilityScores ? instabilityScores.average_precision.toFixed(2) : "—",
      unit: "average precision on later periods",
      versus: instability?.result.metrics.baseline_persistence
        ? `Persistence baseline ${instability.result.metrics.baseline_persistence.average_precision.toFixed(2)}`
        : instability?.result.reason ?? instability?.result.metrics.note,
    },
  ];
  return (
    <div className="model-cards">
      {cards.map((card) => (
        <article className="panel model-card" key={card.title}>
          <div style={{ display: "flex", justifyContent: "space-between", gap: 8, alignItems: "center" }}>
            <h3>{card.title}</h3>
            <span className={`badge ${card.trained ? "badge-ok" : "badge-warn"}`}>{card.trained ? "Trained" : "Abstained"}</span>
          </div>
          <span className="muted small">{card.algo}</span>
          <div><span className="headline">{card.headline}</span> <span className="muted small">{card.unit}</span></div>
          {card.versus && <span className="versus">{card.versus}</span>}
        </article>
      ))}
    </div>
  );
}

const CHAMPION_NAMES: Record<DefectChampion, string> = {
  logistic_regression: "logistic regression",
  random_forest: "random forest",
  gradient_boosting: "gradient boosting",
};

function championName(champion: DefectChampion | null | undefined, capitalised = false) {
  const name = CHAMPION_NAMES[champion ?? "logistic_regression"];
  return capitalised ? name.charAt(0).toUpperCase() + name.slice(1) : name;
}

const forestSeries = { key: "forest", label: "Random forest", color: "var(--warn)" };

function DefectSection({ overview }: { overview: MlOverviewWithInstability }) {
  const { repository } = useRepo();
  const task = overview.tasks.defect_risk;
  if (!task) return null;
  const result = task.result;
  const labels = result.feature_labels;
  return (
    <Panel
      title="Defect-proneness prediction"
      description="Will a bug-fix commit touch this file next? Labels come from commits the intent model classified as fixes (an SZZ-style approach at file level)."
    >
      {task.status !== "trained" ? <Abstained reason={result.reason} /> : (
        <div style={{ display: "grid", gap: 24 }}>
          {result.dataset.train_period && result.dataset.test_period && (
            <div style={{ display: "grid", gap: 6 }}>
              <h3>Temporal split</h3>
              <div className="split-timeline" aria-label="Training and test periods">
                <span style={{ flex: 2, background: "var(--chart-baseline)" }}>History for features</span>
                <span style={{ flex: 1, background: "var(--chart-model)" }}>Train labels</span>
                <span style={{ flex: 1, background: "var(--chart-challenger)" }}>Test labels</span>
              </div>
              <p className="muted small">
                {formatDate(result.dataset.train_period[0])} to {formatDate(result.dataset.test_period[1])}. The model is scored on {result.dataset.test_positive} fix-touched and {result.dataset.test_negative} other files from the final period, using only features from before it.
                {result.dataset.bulk_commits_excluded ? ` ${result.dataset.bulk_commits_excluded} bulk commit(s) excluded.` : ""}
              </p>
            </div>
          )}
          <div className="grid-2">
            <div style={{ display: "grid", gap: 10 }}>
              <h3>Champion and challenger against the baseline</h3>
              {result.metrics.logistic_regression ? (
                <MetricBars
                  caption={`Champion: ${championName(result.champion)}, chosen by average precision. Test base rate ${(result.metrics.test_base_rate ?? 0).toFixed(2)}.`}
                  groups={[
                    { label: "ROC-AUC", values: { baseline: result.metrics.heuristic_baseline?.roc_auc, model: result.metrics.logistic_regression?.roc_auc, forest: result.metrics.random_forest?.roc_auc, challenger: result.metrics.gradient_boosting?.roc_auc } },
                    { label: "Average precision", values: { baseline: result.metrics.heuristic_baseline?.average_precision, model: result.metrics.logistic_regression?.average_precision, forest: result.metrics.random_forest?.average_precision, challenger: result.metrics.gradient_boosting?.average_precision } },
                    { label: "Precision in top 20%", values: { baseline: result.metrics.heuristic_baseline?.precision_at_top20pct, model: result.metrics.logistic_regression?.precision_at_top20pct, forest: result.metrics.random_forest?.precision_at_top20pct, challenger: result.metrics.gradient_boosting?.precision_at_top20pct } },
                  ]}
                  series={[
                    { ...baselineSeries, label: "Heuristic baseline" },
                    { ...modelSeries, label: "Logistic regression" },
                    ...(result.metrics.random_forest ? [forestSeries] : []),
                    { ...challengerSeries, label: "Gradient boosting" },
                  ]}
                />
              ) : <Notice>{result.metrics.note}</Notice>}
            </div>
            <div style={{ display: "grid", gap: 16, alignContent: "start" }}>
              {result.calibration.predicted && result.calibration.observed && (
                <div style={{ display: "grid", gap: 8 }}>
                  <h3>Calibration</h3>
                  <CalibrationPlot observed={result.calibration.observed} predicted={result.calibration.predicted} />
                </div>
              )}
            </div>
          </div>
          <div className="grid-2">
            {result.importance.length > 0 && (
              <div style={{ display: "grid", gap: 10 }}>
                <h3>What the model relies on</h3>
                <p className="muted small">Permutation importance: drop in average precision when a feature is shuffled on the test period.</p>
                <RankedBars items={result.importance.slice(0, 8).map(([name, value]) => ({ label: labels[name] ?? name, value }))} />
              </div>
            )}
            <div style={{ display: "grid", gap: 10 }}>
              <h3>Highest predicted risk</h3>
              {result.contribution_method && <p className="muted small">Factors: {result.contribution_method}</p>}
              <div className="list" style={{ margin: "0 -20px" }}>
                {result.predictions.slice(0, 6).map((item) => (
                  <Link className="list-row" href={`/r/${repository.id}/files?path=${encodeURIComponent(item.path)}`} key={item.path}>
                    <div className="grow">
                      <code className="truncate" style={{ display: "block" }}>{item.path}</code>
                      <small>{item.contributions.slice(0, 2).map(([name, value]) => `${labels[name] ?? name} ${value >= 0 ? "+" : ""}${value.toFixed(2)}`).join(", ")}</small>
                    </div>
                    <span className={`badge ${item.band === "high" ? "badge-eosin" : item.band === "medium" ? "badge-warn" : ""}`}>{item.band}</span>
                    <span className="score">{Math.round(item.probability * 100)}%</span>
                  </Link>
                ))}
              </div>
              <Link className="button button-secondary button-small" href={`/r/${repository.id}/risk`} style={{ justifySelf: "start" }}>Full ranking with explanations</Link>
            </div>
          </div>
        </div>
      )}
    </Panel>
  );
}

function IntentSection({ overview }: { overview: MlOverviewWithInstability }) {
  const task = overview.tasks.commit_intent;
  if (!task) return null;
  const result = task.result;
  const distribution = result.predictions.reduce<Record<string, number>>((acc, item) => {
    acc[item.intent] = (acc[item.intent] ?? 0) + 1;
    return acc;
  }, {});
  return (
    <Panel
      title="Commit intent classifier"
      description={`Trained on ${result.dataset.seed_examples} hand-labelled seed messages plus ${result.dataset.weak_labels} of this repository's commits labelled by their Conventional Commits prefix (prefix removed before training).`}
    >
      <div className="grid-2">
        <div style={{ display: "grid", gap: 16 }}>
          <MetricBars
            caption={`${result.metrics.cv_folds}-fold stratified cross-validation.${result.metrics.repository_holdout ? ` A seed-only model scored ${result.metrics.repository_holdout.macro_f1.toFixed(2)} macro-F1 on ${result.metrics.repository_holdout.examples} of this repository's author-labelled commits.` : ""}`}
            groups={[
              { label: "Macro-F1", values: { baseline: result.metrics.majority_baseline_macro_f1, model: result.metrics.cv_macro_f1 } },
              { label: "Accuracy", values: { model: result.metrics.cv_accuracy } },
            ]}
            series={[{ ...baselineSeries, label: "Majority class" }, { ...modelSeries, label: "Classifier" }]}
          />
          <div style={{ display: "grid", gap: 8 }}>
            <h3>F1 per intent</h3>
            <RankedBars format={(value) => value.toFixed(2)} items={Object.entries(result.metrics.per_class_f1).map(([label, value]) => ({ label, value }))} />
          </div>
        </div>
        <div style={{ display: "grid", gap: 16, alignContent: "start" }}>
          <ConfusionMatrix labels={result.metrics.confusion_matrix.labels} matrix={result.metrics.confusion_matrix.matrix} />
          <div style={{ display: "grid", gap: 6 }}>
            <h3>Most indicative words</h3>
            {Object.entries(result.top_terms).map(([intent, terms]) => (
              <div className="small" key={intent}><span className="intent-badge" data-intent={intent}>{intent}</span> <span className="muted">{terms.slice(0, 6).join(", ").replaceAll("_", " ")}</span></div>
            ))}
          </div>
          <div className="small muted">
            This repository: {Object.entries(distribution).sort((a, b) => b[1] - a[1]).map(([intent, count]) => `${count} ${intent}`).join(", ")}.
          </div>
        </div>
      </div>
    </Panel>
  );
}

function ImpactSection({ overview }: { overview: MlOverviewWithInstability }) {
  const task = overview.tasks.change_impact;
  if (!task) return null;
  const result = task.result;
  const features = Object.keys(result.feature_labels);
  return (
    <Panel title="Change-impact link prediction" description="Predicts which files change together next, from import-graph structure and earlier co-change. Held-out source files are never seen during training.">
      {task.status !== "trained" ? <Abstained reason={result.reason} /> : (
        <div style={{ display: "grid", gap: 20 }}>
          {result.metrics.model ? (
            <MetricBars
              caption={`${result.metrics.test_pairs} held-out pairs, base rate ${(result.metrics.test_base_rate ?? 0).toFixed(2)}. Baselines use a single signal.`}
              groups={[{ label: "ROC-AUC", values: { baseline: result.metrics.baseline_past_cochange?.roc_auc, challenger: result.metrics.baseline_import_distance?.roc_auc, model: result.metrics.model.roc_auc } }]}
              series={[{ ...baselineSeries, label: "Past co-change only" }, { ...challengerSeries, label: "Import distance only" }, { ...modelSeries, label: "Model" }]}
            />
          ) : <Notice>{result.metrics.note}</Notice>}
          <div style={{ display: "grid", gap: 8 }}>
            <h3>Learned weights</h3>
            <DivergingBars items={features.map((name, index) => ({ label: result.feature_labels[name], value: result.coefficients[index] ?? 0 }))} />
          </div>
        </div>
      )}
    </Panel>
  );
}

function RetrievalSection({ overview }: { overview: MlOverviewWithInstability }) {
  const { repository } = useRepo();
  const task = overview.tasks.retrieval;
  if (!task) return null;
  const metrics = task.result.metrics;
  return (
    <Panel
      title="Hybrid retrieval"
      description="Evaluated without human labels: each commit message is a query and the files it changed are the right answers. Commit text is not indexed during evaluation."
      actions={<Link className="button button-ghost button-small" href={`/r/${repository.id}/search`}>Try search</Link>}
    >
      {task.status !== "trained" ? <Abstained reason={task.result.reason} /> : (
        <MetricBars
          caption={`${metrics.queries} queries over ${metrics.documents} files. Random ranking would reach recall@10 of ${(metrics.random_baseline_recall ?? 0).toFixed(2)}. Selected for search, chat, and voice: ${metrics.selected_mode === "bm25" ? "BM25 keywords" : metrics.selected_mode === "semantic" ? "LSA embeddings" : "hybrid fusion"} (best on this repository; hybrid is kept unless another mode wins by more than 0.01).`}
          groups={[
            { label: "Recall@10", values: { baseline: metrics.bm25?.recall_at_10, challenger: metrics.semantic?.recall_at_10, model: metrics.hybrid?.recall_at_10 } },
            { label: "Mean reciprocal rank", values: { baseline: metrics.bm25?.mrr, challenger: metrics.semantic?.mrr, model: metrics.hybrid?.mrr } },
          ]}
          series={[{ ...baselineSeries, label: "BM25 keywords" }, { ...challengerSeries, label: "LSA embeddings" }, { ...modelSeries, label: "Hybrid (fused)" }]}
        />
      )}
    </Panel>
  );
}

function ModulesSection({ overview }: { overview: MlOverviewWithInstability }) {
  const task = overview.tasks.modules;
  if (!task) return null;
  const result = task.result;
  return (
    <Panel title="Learned modules" description="Louvain communities on a graph of imports and co-changes, compared with grouping by directory." flush>
      {task.status !== "trained" ? <div className="panel-body"><Abstained reason={result.reason} /></div> : (
        <>
          <div className="panel-body">
            <MetricBars
              caption={`${result.metrics.communities} communities over ${result.metrics.clustered_files} connected files; ${result.metrics.isolated_files} files had no edges. Higher modularity means denser links inside groups than between them.`}
              groups={[{ label: "Modularity", values: { baseline: result.metrics.modularity_directory_baseline, model: result.metrics.modularity_learned } }]}
              series={[{ ...baselineSeries, label: `Directories (${result.metrics.directory_groups})` }, { ...modelSeries, label: "Learned" }]}
            />
          </div>
          <div className="list">
            {result.modules.slice(0, 8).map((module) => (
              <div className="list-row" key={module.name + module.files[0]}>
                <div className="grow"><strong className="truncate" style={{ display: "block" }}>{module.name}</strong><small className="truncate">{module.files.slice(0, 3).join(", ")}{module.files.length > 3 ? `, +${module.files.length - 3}` : ""}</small></div>
                <span className="muted small">{module.files.length} files</span>
                <span className="badge" title="Share of edge weight that stays inside the module">{Math.round(module.cohesion * 100)}% internal</span>
              </div>
            ))}
          </div>
        </>
      )}
    </Panel>
  );
}

function AnomalySection({ overview }: { overview: MlOverviewWithInstability }) {
  const { inventory } = useRepo();
  const task = overview.tasks.anomalies;
  if (!task) return null;
  const result = task.result;
  const messages = new Map(inventory.data?.commits.map((commit) => [commit.sha, commit.message.split("\n")[0]]) ?? []);
  return (
    <Panel title="Unusual commits" description="Commits an isolation forest separates quickly from the rest, with the features that stand out. Review prompts, not findings." flush>
      {task.status !== "trained" ? <div className="panel-body"><Abstained reason={result.reason} /></div> : result.anomalies.length === 0 ? <Empty title="Nothing stands out">No commit was isolated as unusual.</Empty> : (
        <div className="list">
          {result.anomalies.slice(0, 8).map((item) => (
            <div className="list-row" key={item.sha} style={{ alignItems: "flex-start" }}>
              <div className="grow" style={{ display: "grid", gap: 4 }}>
                <span className="truncate">{messages.get(item.sha) ?? <code>{shortSha(item.sha)}</code>}</span>
                <small>{item.reasons.join("; ")}</small>
                <EvidenceChips ids={[`commit:${item.sha}`]} />
              </div>
              <span className="score" title="Isolation score">{item.score.toFixed(2)}</span>
            </div>
          ))}
        </div>
      )}
    </Panel>
  );
}

function InstabilitySection({ overview }: { overview: MlOverviewWithInstability }) {
  const { repository } = useRepo();
  const task = overview.tasks.instability;
  if (!task) return null;
  const result = task.result;
  const { dataset, metrics } = result;
  const weekly = dataset.period_kind === "week";
  const period = weekly ? "week" : `window of ${dataset.period_size ?? "?"} commits`;
  const labels = result.feature_labels;
  return (
    <Panel
      title="Component instability forecast"
      description={`Which components (directories) will a bug-fix commit touch next ${weekly ? "week" : "period"}? A logistic regression reads each component's last ${dataset.window ?? 4} periods of commits, fixes, churn, and authors: a windowed sequence model, not a recurrent network. Forecasts are inferred, not findings.`}
    >
      {task.status !== "trained" ? <Abstained reason={result.reason} /> : (
        <div className="grid-2">
          <div style={{ display: "grid", gap: 10, alignContent: "start" }}>
            <h3>Held-out later periods against baselines</h3>
            {metrics.model ? (
              <MetricBars
                caption={`One period is a ${period}; ${dataset.periods} periods, ${dataset.components} components. Scored on ${dataset.test_samples} component-periods${dataset.test_period?.[0] ? ` from ${formatDate(dataset.test_period[0])} on` : ""} (base rate ${(metrics.test_base_rate ?? 0).toFixed(2)}), never seen in training. Persistence predicts that the next period repeats this one.`}
                groups={[
                  { label: "ROC-AUC", values: { baseline: metrics.baseline_persistence?.roc_auc, challenger: metrics.baseline_historical_rate?.roc_auc, model: metrics.model.roc_auc } },
                  { label: "Average precision", values: { baseline: metrics.baseline_persistence?.average_precision, challenger: metrics.baseline_historical_rate?.average_precision, model: metrics.model.average_precision } },
                  { label: "F1", values: { baseline: metrics.baseline_persistence?.f1, challenger: metrics.baseline_historical_rate?.f1, model: metrics.model.f1 } },
                ]}
                series={[{ ...baselineSeries, label: "Persistence" }, { ...challengerSeries, label: "Historical fix rate" }, { ...modelSeries, label: "Windowed model" }]}
              />
            ) : <Notice>{metrics.note}</Notice>}
          </div>
          <div style={{ display: "grid", gap: 10, alignContent: "start" }}>
            <h3>Most likely to need a fix next {weekly ? "week" : "period"}</h3>
            <p className="muted small">Bars show commits in each of the last {dataset.window ?? 4} periods (fixes in red). Factors are log-odds contributions.</p>
            <div className="list" style={{ margin: "0 -20px" }}>
              {result.predictions.slice(0, 6).map((item) => (
                <div className="list-row" key={item.component} style={{ alignItems: "flex-start" }}>
                  <div className="grow" style={{ display: "grid", gap: 4 }}>
                    <code className="truncate" style={{ display: "block" }}>{item.component}</code>
                    <small>
                      {item.files} files{item.fixed_last_period ? " · fixed in the latest period" : ""}
                      {item.contributions.length > 0 && ` · ${item.contributions.slice(0, 2).map(([name, value]) => `${labels[name] ?? name} ${value >= 0 ? "+" : ""}${value.toFixed(2)}`).join(", ")}`}
                    </small>
                    {item.evidence_shas.length > 0 && <EvidenceChips ids={item.evidence_shas.slice(0, 3).map((sha) => `commit:${sha}`)} />}
                  </div>
                  <ActivitySequence periods={item.recent_periods} />
                  <span className={`badge ${item.band === "high" ? "badge-eosin" : item.band === "medium" ? "badge-warn" : ""}`}>{item.band}</span>
                  <span className="score" title="Inferred probability">{Math.round(item.probability * 100)}%</span>
                </div>
              ))}
            </div>
            <Link className="button button-secondary button-small" href={`/r/${repository.id}/risk`} style={{ justifySelf: "start" }}>File-level risk ranking</Link>
            <p className="muted small">Label: a commit the intent model classified as a fix touches the component in the next period. A forecast is a prompt to review, not proof of a defect.</p>
          </div>
        </div>
      )}
    </Panel>
  );
}

function ActivitySequence({ periods }: { periods: Array<[number, number]> }) {
  const peak = Math.max(1, ...periods.map(([other, fixes]) => other + fixes));
  const summary = periods.map(([other, fixes]) => `${other + fixes} commits, ${fixes} fixes`).join("; ");
  return (
    <span aria-label={`Recent periods, oldest first: ${summary}`} role="img" style={{ display: "inline-flex", alignItems: "flex-end", gap: 2, height: 24 }} title={summary}>
      {periods.map(([other, fixes], index) => (
        <span key={index} style={{ display: "inline-flex", flexDirection: "column-reverse", width: 6, height: 24, borderBottom: "2px solid var(--chart-baseline)" }}>
          <span style={{ height: `${(fixes / peak) * 100}%`, background: "var(--chart-challenger)" }} />
          <span style={{ height: `${(other / peak) * 100}%`, background: "var(--chart-model)" }} />
        </span>
      ))}
    </span>
  );
}
