import type { AnswerLanguage, GroundedAnswer, VoiceName, VoiceSession } from "@code-genome/contracts";

import { api } from "./api";

/*
 * Browser client for the Gemini Live API (gemini-3.8-live).
 *
 * The API key never reaches the browser: the backend mints a single-use ephemeral
 * token whose constraints lock the system instruction and tool surface. Microphone
 * audio is streamed as 16 kHz little-endian PCM; the model replies with 24 kHz PCM.
 * The only tool the model can call is repository evidence search, which is proxied to
 * the audited grounded-answer endpoint so spoken answers keep their citations.
 */

export type VoiceStatus = "idle" | "connecting" | "listening" | "speaking" | "thinking" | "ended" | "error";

export type VoiceEvent =
  | { type: "status"; status: VoiceStatus; detail?: string }
  | { type: "transcript"; role: "user" | "assistant"; text: string; final: boolean }
  | { type: "tool"; question: string; answer: GroundedAnswer | null; error?: string; label?: string; text?: string }
  | { type: "session"; session: VoiceSession };

const EVIDENCE_TOOL = "search_repository_evidence";
const INPUT_RATE = 16000;
const OUTPUT_RATE = 24000;
/** Matches the server's question length limit. */
export const MAX_TEXT_LENGTH = 2000;

const workletSource = `
class PcmDownsampler extends AudioWorkletProcessor {
  constructor(options) {
    super();
    this.ratio = sampleRate / ${INPUT_RATE};
    this.buffer = [];
    this.position = 0;
    this.chunk = ${INPUT_RATE} / 10;
  }
  process(inputs) {
    const channel = inputs[0] && inputs[0][0];
    if (!channel) return true;
    while (this.position < channel.length) {
      const index = Math.floor(this.position);
      const next = Math.min(index + 1, channel.length - 1);
      const fraction = this.position - index;
      this.buffer.push(channel[index] * (1 - fraction) + channel[next] * fraction);
      this.position += this.ratio;
    }
    this.position -= channel.length;
    if (this.buffer.length >= this.chunk) {
      const pcm = new Int16Array(this.buffer.length);
      for (let i = 0; i < this.buffer.length; i++) {
        const s = Math.max(-1, Math.min(1, this.buffer[i]));
        pcm[i] = s < 0 ? s * 0x8000 : s * 0x7fff;
      }
      this.port.postMessage(pcm.buffer, [pcm.buffer]);
      this.buffer = [];
    }
    return true;
  }
}
registerProcessor("pcm-downsampler", PcmDownsampler);
`;

function toBase64(buffer: ArrayBuffer): string {
  const bytes = new Uint8Array(buffer);
  let binary = "";
  for (let i = 0; i < bytes.length; i += 0x8000) {
    binary += String.fromCharCode(...bytes.subarray(i, i + 0x8000));
  }
  return btoa(binary);
}

function fromBase64Pcm(data: string): Float32Array<ArrayBuffer> {
  const binary = atob(data);
  const samples = new Float32Array(binary.length >> 1);
  for (let i = 0; i < samples.length; i++) {
    const low = binary.charCodeAt(i * 2);
    const high = binary.charCodeAt(i * 2 + 1);
    let value = (high << 8) | low;
    if (value >= 0x8000) value -= 0x10000;
    samples[i] = value / 0x8000;
  }
  return samples;
}

type LiveMessage = {
  setupComplete?: object;
  serverContent?: {
    modelTurn?: { parts?: Array<{ inlineData?: { mimeType?: string; data?: string }; text?: string }> };
    inputTranscription?: { text?: string };
    outputTranscription?: { text?: string };
    turnComplete?: boolean;
    interrupted?: boolean;
  };
  toolCall?: { functionCalls?: Array<{ id?: string; name?: string; args?: Record<string, unknown> }> };
  goAway?: { timeLeft?: string };
};

/** A custom tool surface (the workspace assistant) instead of repository evidence search. */
export type VoiceToolHandler = (name: string, args: Record<string, unknown>) => Promise<Record<string, unknown>>;

export type LiveVoiceOptions = {
  createSession?: () => Promise<VoiceSession>;
  runTool?: VoiceToolHandler;
};

export class LiveVoiceAgent {
  private socket: WebSocket | null = null;
  private inputContext: AudioContext | null = null;
  private outputContext: AudioContext | null = null;
  private stream: MediaStream | null = null;
  private worklet: AudioWorkletNode | null = null;
  private sources = new Set<AudioBufferSourceNode>();
  private playhead = 0;
  private ready = false;
  private muted = false;
  private closedByUser = false;
  private failed = false;
  readonly inputAnalyser: { node: AnalyserNode | null } = { node: null };
  readonly outputAnalyser: { node: AnalyserNode | null } = { node: null };

  constructor(
    private readonly repositoryId: string,
    private readonly voice: VoiceName,
    private readonly language: AnswerLanguage,
    private readonly emit: (event: VoiceEvent) => void,
    private readonly options: LiveVoiceOptions = {},
  ) {}

  async start(): Promise<void> {
    this.emit({ type: "status", status: "connecting" });
    let stream: MediaStream;
    try {
      // Ask for the microphone before minting a token: the token is single-use and
      // expires quickly if permission is denied.
      stream = await navigator.mediaDevices.getUserMedia({
        audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true, autoGainControl: true },
      });
    } catch {
      if (this.closedByUser) return;
      this.fail("Microphone access was blocked. Allow the microphone for this site and try again.");
      return;
    }
    // The user may have ended the call (or left the page) while the permission prompt was open.
    if (this.closedByUser) {
      stream.getTracks().forEach((track) => track.stop());
      return;
    }
    this.stream = stream;
    let session: VoiceSession;
    try {
      session = this.options.createSession
        ? await this.options.createSession()
        : await api.createVoiceSession(this.repositoryId, this.voice, this.language);
    } catch (caught) {
      if (this.closedByUser) {
        this.teardownAudio();
        return;
      }
      this.fail(caught instanceof Error ? caught.message : "The voice session could not start.");
      return;
    }
    if (this.closedByUser) {
      this.teardownAudio();
      return;
    }
    this.emit({ type: "session", session });
    this.outputContext = new AudioContext({ sampleRate: OUTPUT_RATE });
    const outputAnalyser = this.outputContext.createAnalyser();
    outputAnalyser.fftSize = 256;
    outputAnalyser.connect(this.outputContext.destination);
    this.outputAnalyser.node = outputAnalyser;

    const url = `${session.websocket_url}?access_token=${encodeURIComponent(session.token)}`;
    const socket = new WebSocket(url);
    this.socket = socket;
    socket.onopen = () => {
      if (this.closedByUser) {
        socket.close(1000, "user ended session");
        return;
      }
      socket.send(JSON.stringify({ setup: session.setup }));
    };
    socket.onmessage = (message) => {
      if (this.closedByUser) return;
      void this.receive(message.data);
    };
    socket.onerror = () => {
      if (this.closedByUser) return;
      this.fail("The voice connection failed.");
    };
    socket.onclose = (event) => {
      this.teardownAudio();
      // A failure already reported its own error; do not replace it with "ended".
      if (this.failed) return;
      if (this.closedByUser) {
        this.emit({ type: "status", status: "ended" });
      } else if (event.code !== 1000) {
        this.fail(event.reason ? `Voice session closed: ${event.reason}` : "The voice session closed unexpectedly.");
      } else {
        this.emit({ type: "status", status: "ended", detail: "The voice session ended." });
      }
    };
  }

  setMuted(muted: boolean) {
    this.muted = muted;
    if (muted && this.ready) this.send({ realtimeInput: { audioStreamEnd: true } });
  }

  sendText(text: string) {
    if (!this.ready || this.closedByUser) return;
    text = text.slice(0, MAX_TEXT_LENGTH);
    this.send({ realtimeInput: { text } });
    this.emit({ type: "transcript", role: "user", text, final: true });
  }

  stop() {
    const alreadyClosed = this.closedByUser;
    this.closedByUser = true;
    if (this.socket && this.socket.readyState <= WebSocket.OPEN) this.socket.close(1000, "user ended session");
    this.teardownAudio();
    // Keep a reported failure visible; only announce "ended" for a call that was still running.
    if (!alreadyClosed && !this.failed) this.emit({ type: "status", status: "ended" });
  }

  private send(payload: object) {
    if (this.socket?.readyState === WebSocket.OPEN) this.socket.send(JSON.stringify(payload));
  }

  private fail(detail: string) {
    if (this.failed) return;
    this.failed = true;
    this.closedByUser = true;
    if (this.socket && this.socket.readyState <= WebSocket.OPEN) this.socket.close();
    this.teardownAudio();
    this.emit({ type: "status", status: "error", detail });
  }

  private async startMicrophone() {
    if (!this.stream || this.closedByUser) return;
    const context = new AudioContext();
    this.inputContext = context;
    const moduleUrl = URL.createObjectURL(new Blob([workletSource], { type: "application/javascript" }));
    try {
      await context.audioWorklet.addModule(moduleUrl);
    } finally {
      URL.revokeObjectURL(moduleUrl);
    }
    // teardownAudio() may have run while the module loaded; never reopen a stopped stream.
    if (this.closedByUser || !this.stream || this.inputContext !== context) return;
    const source = context.createMediaStreamSource(this.stream);
    const analyser = context.createAnalyser();
    analyser.fftSize = 256;
    this.inputAnalyser.node = analyser;
    this.worklet = new AudioWorkletNode(context, "pcm-downsampler");
    this.worklet.port.onmessage = (event: MessageEvent<ArrayBuffer>) => {
      if (this.muted || !this.ready) return;
      this.send({
        realtimeInput: { audio: { mimeType: `audio/pcm;rate=${INPUT_RATE}`, data: toBase64(event.data) } },
      });
    };
    source.connect(analyser);
    source.connect(this.worklet);
  }

  private async receive(raw: unknown) {
    const text = typeof raw === "string" ? raw : raw instanceof Blob ? await raw.text() : null;
    if (!text) return;
    let message: LiveMessage;
    try {
      message = JSON.parse(text) as LiveMessage;
    } catch {
      return;
    }
    if (message.setupComplete) {
      if (this.closedByUser) return;
      this.ready = true;
      try {
        await this.startMicrophone();
      } catch {
        if (!this.closedByUser) this.fail("The microphone could not be started in this browser.");
        return;
      }
      // startMicrophone awaits the worklet module; the call may have ended meanwhile.
      if (this.closedByUser) {
        this.teardownAudio();
        return;
      }
      this.emit({ type: "status", status: "listening" });
    }
    const content = message.serverContent;
    if (content) {
      if (content.interrupted) this.stopPlayback();
      if (content.inputTranscription?.text) {
        this.emit({ type: "transcript", role: "user", text: content.inputTranscription.text, final: false });
      }
      if (content.outputTranscription?.text) {
        this.emit({ type: "transcript", role: "assistant", text: content.outputTranscription.text, final: false });
      }
      for (const part of content.modelTurn?.parts ?? []) {
        if (part.inlineData?.data && part.inlineData.mimeType?.startsWith("audio/pcm")) {
          this.play(fromBase64Pcm(part.inlineData.data));
        }
      }
      if (content.turnComplete) {
        this.emit({ type: "transcript", role: "assistant", text: "", final: true });
        this.emit({ type: "status", status: "listening" });
      }
    }
    if (message.toolCall?.functionCalls?.length) await this.runTools(message.toolCall.functionCalls);
    if (message.goAway) {
      this.emit({ type: "status", status: "listening", detail: "The session is about to reach its time limit." });
    }
  }

  private async runTools(calls: NonNullable<NonNullable<LiveMessage["toolCall"]>["functionCalls"]>) {
    this.emit({ type: "status", status: "thinking" });
    const custom = this.options.runTool;
    const functionResponses = await Promise.all(
      calls.map(async (call) => {
        if (custom) {
          try {
            return { id: call.id, name: call.name, response: await custom(call.name ?? "", call.args ?? {}) };
          } catch (caught) {
            return { id: call.id, name: call.name, response: { error: caught instanceof Error ? caught.message : "The tool failed." } };
          }
        }
        const question = typeof call.args?.question === "string" ? call.args.question.slice(0, 2000) : "";
        if (call.name !== EVIDENCE_TOOL || question.trim().length < 2) {
          return { id: call.id, name: call.name, response: { error: "Unsupported tool call." } };
        }
        try {
          const answer = await api.ask(this.repositoryId, question, "en", "voice");
          this.emit({ type: "tool", question, answer });
          return {
            id: call.id,
            name: call.name,
            response: {
              answer: answer.answer,
              evidence_ids: answer.evidence_ids,
              limitations: answer.limitations,
              snapshot_sha: answer.scope.snapshot_sha ?? null,
            },
          };
        } catch (caught) {
          const error = caught instanceof Error ? caught.message : "Evidence search failed.";
          this.emit({ type: "tool", question, answer: null, error });
          return { id: call.id, name: call.name, response: { error } };
        }
      }),
    );
    if (this.closedByUser) return;
    this.send({ toolResponse: { functionResponses } });
  }

  private play(samples: Float32Array<ArrayBuffer>) {
    const context = this.outputContext;
    const analyser = this.outputAnalyser.node;
    if (!context || !analyser || samples.length === 0) return;
    const buffer = context.createBuffer(1, samples.length, OUTPUT_RATE);
    buffer.copyToChannel(samples, 0);
    const source = context.createBufferSource();
    source.buffer = buffer;
    source.connect(analyser);
    const startAt = Math.max(context.currentTime + 0.02, this.playhead);
    source.start(startAt);
    this.playhead = startAt + buffer.duration;
    this.sources.add(source);
    this.emit({ type: "status", status: "speaking" });
    source.onended = () => {
      this.sources.delete(source);
    };
  }

  private stopPlayback() {
    for (const source of this.sources) {
      try {
        source.stop();
      } catch {
        // Already stopped.
      }
    }
    this.sources.clear();
    this.playhead = 0;
  }

  private teardownAudio() {
    this.ready = false;
    this.stopPlayback();
    this.worklet?.port.close();
    this.worklet?.disconnect();
    this.worklet = null;
    this.stream?.getTracks().forEach((track) => track.stop());
    this.stream = null;
    void this.inputContext?.close().catch(() => undefined);
    void this.outputContext?.close().catch(() => undefined);
    this.inputContext = null;
    this.outputContext = null;
    this.inputAnalyser.node = null;
    this.outputAnalyser.node = null;
  }
}
