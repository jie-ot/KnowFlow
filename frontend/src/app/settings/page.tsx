"use client";

import { CheckCircle2, RotateCcw, Trash2, XCircle } from "lucide-react";
import { useEffect, useState } from "react";
import useSWR, { mutate as globalMutate } from "swr";
import { api, ApiError, fetcher, ModelConfig, NoteSummary } from "@/lib/api";
import { Empty, Modal, PageHeader, Spinner, useConfirm, useToast } from "@/components/ui";

type Tab = "model" | "ai" | "trash";

export default function SettingsPage() {
  const [tab, setTab] = useState<Tab>("model");
  return (
    <div className="max-w-[760px] mx-auto px-10 py-12">
      <PageHeader title="设置" actions={
        <div className="seg">
          <button data-on={tab === "model"} onClick={() => setTab("model")}>大模型</button>
          <button data-on={tab === "ai"} onClick={() => setTab("ai")}>AI 选项</button>
          <button data-on={tab === "trash"} onClick={() => setTab("trash")}>回收站</button>
        </div>
      } />
      {tab === "model" && <Models />}
      {tab === "ai" && <AiOptions />}
      {tab === "trash" && <Trash />}
    </div>
  );
}

function Models() {
  const { data: configs, mutate } = useSWR<ModelConfig[]>("/api/ai/configs", fetcher);
  const [editing, setEditing] = useState<ModelConfig | null | "new">(null);
  const confirm = useConfirm();
  const refresh = () => { mutate(); globalMutate("/api/ai/status"); };

  return (
    <div>
      <p className="text-[14px] text-muted mb-6 leading-relaxed">
        支持任何 OpenAI 兼容接口（DeepSeek、阿里云百炼、Kimi 等）。API Key 只保存在这台电脑的本地数据库里，页面上只显示后 4 位，不会发给除你填写的服务商以外的任何地方。
      </p>
      <div className="border-y border-rule divide-y divide-rule">
        {configs?.map((c) => (
          <div key={c.id} className="flex items-center gap-4 py-4">
            <div className="flex-1 min-w-0">
              <p className="text-[14.5px] font-medium flex items-center gap-2">
                {c.name}{c.is_default && <span className="chip !h-5 !text-[11px]" style={{ background: "var(--accent-soft)", color: "var(--accent)" }}>默认</span>}
              </p>
              <p className="text-[12.5px] text-muted mt-0.5 truncate">{c.model}，{c.base_url}，Key {c.api_key_masked}</p>
              <p className="text-[12px] text-faint mt-0.5">{c.consented ? "已同意向该服务商发送内容" : "首次使用时会请你确认发送范围"}</p>
            </div>
            {!c.is_default && <button className="btn" onClick={async () => { await api(`/api/ai/configs/${c.id}/default`, { method: "POST" }); refresh(); }}>设为默认</button>}
            <button className="btn btn-outline" onClick={() => setEditing(c)}>编辑</button>
            <button className="btn btn-icon text-muted" aria-label="删除配置" onClick={async () => {
              if (await confirm({ title: `删除「${c.name}」？`, confirm: "删除", danger: true })) { await api(`/api/ai/configs/${c.id}`, { method: "DELETE" }); refresh(); }
            }}><Trash2 size={15} /></button>
          </div>
        ))}
      </div>
      {configs?.length === 0 && <Empty title="还没有连接大模型">添加一个配置，就能使用 AI 整理、摘要、提炼和拆任务。</Empty>}
      <button className="btn btn-primary mt-5" onClick={() => setEditing("new")}>添加配置</button>
      <ConfigDialog config={editing} onClose={() => setEditing(null)} onSaved={refresh} />
    </div>
  );
}

function ConfigDialog({ config, onClose, onSaved }: { config: ModelConfig | null | "new"; onClose: () => void; onSaved: () => void }) {
  const isNew = config === "new";
  const [f, setF] = useState({ name: "", base_url: "", api_key: "", model: "" });
  const [test, setTest] = useState<{ ok: boolean; message: string; detail?: string } | null>(null);
  const [testing, setTesting] = useState(false);
  const toast = useToast();
  useEffect(() => {
    if (config === "new") setF({ name: "DeepSeek", base_url: "https://api.deepseek.com", api_key: "", model: "deepseek-flash" });
    else if (config) setF({ name: config.name, base_url: config.base_url, api_key: "", model: config.model });
    setTest(null);
  }, [config]);
  const set = (k: keyof typeof f) => (e: { target: { value: string } }) => { setF((x) => ({ ...x, [k]: e.target.value })); setTest(null); };

  const runTest = async () => {
    setTesting(true);
    try {
      const r = await api<{ ok: boolean; message: string; detail?: string }>("/api/ai/configs/test", {
        method: "POST",
        json: { config_id: isNew ? null : (config as ModelConfig).id, base_url: f.base_url, api_key: f.api_key || null, model: f.model },
      });
      setTest(r);
    } catch (e) {
      setTest({ ok: false, message: (e as ApiError).message });
    } finally {
      setTesting(false);
    }
  };
  const save = async () => {
    try {
      if (isNew) await api("/api/ai/configs", { method: "POST", json: { ...f, is_default: true } });
      else await api(`/api/ai/configs/${(config as ModelConfig).id}`, { method: "PATCH", json: f });
      toast("已保存");
      onSaved();
      onClose();
    } catch (e) {
      toast((e as ApiError).message, "bad");
    }
  };
  const valid = f.name.trim() && f.base_url.trim() && f.model.trim() && (!isNew || f.api_key.trim());
  return (
    <Modal open={config !== null} onClose={onClose} title={isNew ? "添加大模型配置" : "编辑配置"} width={520}
      footer={<>
        <button className="btn btn-outline mr-auto" disabled={!valid || testing} onClick={runTest}>{testing && <Spinner size={12} />}测试连接</button>
        <button className="btn btn-outline" onClick={onClose}>取消</button>
        <button className="btn btn-primary" disabled={!valid} onClick={save}>保存</button>
      </>}>
      <div className="p-5 space-y-4">
        <div><label className="label" htmlFor="c-name">名称</label><input id="c-name" className="input" value={f.name} onChange={set("name")} /></div>
        <div><label className="label" htmlFor="c-url">Base URL</label><input id="c-url" className="input font-mono text-[13px]" value={f.base_url} onChange={set("base_url")} placeholder="https://api.deepseek.com" /></div>
        <div>
          <label className="label" htmlFor="c-key">API Key</label>
          <input id="c-key" type="password" autoComplete="off" className="input font-mono text-[13px]" value={f.api_key} onChange={set("api_key")}
            placeholder={isNew ? "sk-…" : `留空则保持原来的 Key（${(config as ModelConfig | null)?.api_key_masked || ""}）`} />
        </div>
        <div>
          <label className="label" htmlFor="c-model">模型名称</label>
          <input id="c-model" className="input font-mono text-[13px]" value={f.model} onChange={set("model")} />
          <p className="text-[12px] text-faint mt-1.5">模型名会随服务商更新而变化，以服务商文档为准。</p>
        </div>
        {test && (
          <div className={`flex gap-2 rounded-xl px-4 py-3 text-[13px] ${test.ok ? "bg-accent-soft text-accent" : "bg-bad-soft text-bad"}`}>
            {test.ok ? <CheckCircle2 size={16} /> : <XCircle size={16} />}
            <div><p className="font-medium">{test.message}</p>{test.detail && <p className="mt-1 text-muted break-all">服务商返回：{test.detail}</p>}</div>
          </div>
        )}
      </div>
    </Modal>
  );
}

type Settings = { max_input_chars: number; idle_timeout_s: number; total_timeout_s: number; temperature: number };

function AiOptions() {
  const { data, mutate } = useSWR<Settings>("/api/settings", fetcher);
  const [f, setF] = useState<Settings | null>(null);
  const toast = useToast();
  useEffect(() => { if (data) setF(data); }, [data]);
  if (!f) return null;
  const save = async () => {
    try {
      await api("/api/settings", { method: "PATCH", json: f });
      mutate();
      toast("已保存");
    } catch (e) {
      toast((e as ApiError).message, "bad");
    }
  };
  const field = (k: keyof Settings, label: string, hint: string, step = 1) => (
    <div className="flex items-center gap-6 py-4">
      <div className="flex-1">
        <label className="text-[14px] font-medium" htmlFor={`s-${k}`}>{label}</label>
        <p className="text-[12.5px] text-muted mt-0.5">{hint}</p>
      </div>
      <input id={`s-${k}`} type="number" step={step} className="input w-28 text-right tabular-nums" value={f[k]}
        onChange={(e) => setF({ ...f, [k]: Number(e.target.value) })} />
    </div>
  );
  return (
    <div>
      <div className="border-y border-rule divide-y divide-rule">
        {field("max_input_chars", "单次输入字数上限", "超过时提示分段，不会静默截断。默认 16000 字")}
        {field("idle_timeout_s", "无响应超时（秒）", "等待第一个字或两段数据之间超过这个时间就停止。默认 60")}
        {field("total_timeout_s", "总时长上限（秒）", "一次请求最长等待时间。默认 120")}
        {field("temperature", "温度", "越低越稳定、越贴近原文。默认 0.3", 0.1)}
      </div>
      <button className="btn btn-primary mt-5" onClick={save}>保存</button>
    </div>
  );
}

function Trash() {
  const { data, mutate } = useSWR<NoteSummary[]>("/api/trash", fetcher);
  const confirm = useConfirm();
  const toast = useToast();
  return (
    <div>
      <p className="text-[14px] text-muted mb-6">删除的笔记连同由它生成的卡片会在这里保留 30 天，之后自动清除。</p>
      <div className="border-y border-rule divide-y divide-rule">
        {data?.map((n) => (
          <div key={n.id} className="flex items-center gap-4 py-3.5">
            <div className="flex-1 min-w-0">
              <p className="text-[14px] font-medium truncate">{n.title}</p>
              <p className="text-[12.5px] text-muted mt-0.5">还剩 {n.days_left} 天{n.card_count ? `，含 ${n.card_count} 张卡片` : ""}</p>
            </div>
            <button className="btn btn-outline" onClick={async () => {
              await api(`/api/notes/${n.id}/restore`, { method: "POST" }); mutate(); globalMutate("/api/courses"); toast("已恢复");
            }}><RotateCcw size={13} />恢复</button>
            <button className="btn btn-danger" onClick={async () => {
              if (await confirm({ title: "彻底删除？", body: "删除后无法恢复。", confirm: "彻底删除", danger: true })) {
                await api(`/api/notes/${n.id}/purge`, { method: "DELETE" }); mutate();
              }
            }}>彻底删除</button>
          </div>
        ))}
      </div>
      {data?.length === 0 && <Empty title="回收站是空的" />}
    </div>
  );
}
