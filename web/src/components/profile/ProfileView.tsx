import { useEffect, useState } from "react";
import { useProfileStore } from "../../stores/profileStore";
import { useSuggestionsStore } from "../../stores/suggestionsStore";
import type { ProfileDTO, ProfileUpdate } from "../../api/client";
import { SuggestionsModal } from "./SuggestionsModal";
import "./ProfileView.css";

/** the five dimensions, in canonical order (mirrors profile._FIELD_TO_TITLE
 * in the backend, so the front-end titles match the injected ## headings). */
const DIMENSIONS: { readonly key: keyof ProfileUpdate; readonly title: string }[] = [
  { key: "ability", title: "能力水平" },
  { key: "methodology", title: "方法论偏好" },
  { key: "expression", title: "表达风格" },
  { key: "goal", title: "长程目标" },
  { key: "other", title: "其他" },
];

function emptyDraft(): ProfileUpdate {
  return { ability: "", methodology: "", expression: "", goal: "", other: "" };
}

/** copy the five editable dims out of the loaded profile into an edit draft */
function profileToDraft(p: ProfileDTO): ProfileUpdate {
  return {
    ability: p.ability,
    methodology: p.methodology,
    expression: p.expression,
    goal: p.goal,
    other: p.other,
  };
}

/**
 * ProfileView — the fifth sidebar tab (方案 B single-pane document).
 *
 * Renders the five-dim profile as a centered "about me" document. Read mode
 * shows each section's body; clicking 编辑画像 flips all five into textareas
 * (editor-shell sunshine ring) and saves them in one PUT. Save failure is
 * shown explicitly (C-6) and keeps the draft so the user can retry. Cold
 * start (empty profile) shows five empty sections + the edit button.
 *
 * slice-050: the doc-toolbar shows a 「查看 AI 建议」 entry when there are
 * pending suggestions, opening the SuggestionsModal (accept appends to the
 * dims, never replacing).
 */
export function ProfileView() {
  const { profile, loading, error, fetchProfile, updateProfile } =
    useProfileStore();
  const { suggestions, fetchSuggestions } = useSuggestionsStore();
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState<ProfileUpdate>(emptyDraft());
  const [saveError, setSaveError] = useState<string | null>(null);
  const [modalOpen, setModalOpen] = useState(false);

  // fetchProfile / fetchSuggestions are stable zustand actions (defined once at
  // create), so this effect runs once on mount.
  useEffect(() => {
    fetchProfile();
    fetchSuggestions();
  }, [fetchProfile, fetchSuggestions]);

  const startEdit = () => {
    setDraft(profile ? profileToDraft(profile) : emptyDraft());
    setSaveError(null);
    setEditing(true);
  };

  const cancelEdit = () => {
    setEditing(false);
    setSaveError(null);
  };

  const save = async () => {
    try {
      await updateProfile(draft);
      setEditing(false);
      setSaveError(null);
    } catch (err) {
      // C-6: surface the failure explicitly, keep the draft so the user can retry
      setSaveError(err instanceof Error ? err.message : "保存失败");
    }
  };

  const updateDim = (key: keyof ProfileUpdate, value: string) => {
    setDraft((d) => ({ ...d, [key]: value }));
  };

  if (!profile) {
    return (
      <div className="profile-view">
        <div className="doc-wrap">
          <p className="profile-loading">{error ?? "加载中…"}</p>
        </div>
      </div>
    );
  }

  return (
    <div className="profile-view">
      <div className="doc-wrap">
        <header className="doc-header">
          <div className="eyebrow">关于我 · 用户画像</div>
          <h1>让 trowel 懂我是谁</h1>
          <p className="intro">
            下面五个方面是 trowel
            每次开新会话时心里的"我"——它会据此决定解释多深、怎么干活、怎么说话。
          </p>
          {!editing && (
            <div className="doc-toolbar">
              <div className="meta">
                {profile.updated ? `更新于 ${profile.updated}` : "尚未保存"}
                {suggestions.length > 0 && (
                  <span
                    className="badge"
                    data-testid="profile-suggestions-count"
                  >
                    {" "}
                    · ✦ {suggestions.length} 条 AI 校准建议
                  </span>
                )}
              </div>
              <div className="doc-toolbar__btns">
                {suggestions.length > 0 && (
                  <button
                    className="btn btn--ghost"
                    onClick={() => setModalOpen(true)}
                    data-testid="profile-suggestions-button"
                  >
                    查看 AI 建议
                  </button>
                )}
                <button
                  className="btn btn--secondary"
                  onClick={startEdit}
                  data-testid="profile-edit-button"
                >
                  编辑画像
                </button>
              </div>
            </div>
          )}
        </header>

        {editing && (
          <div className="edit-bar">
            <span className="lbl">正在编辑画像 · 改完一次性保存</span>
            <div className="acts">
              <button
                className="btn btn--secondary"
                onClick={cancelEdit}
                disabled={loading}
                data-testid="profile-cancel-button"
              >
                取消
              </button>
              <button
                className="btn btn--primary"
                onClick={save}
                disabled={loading}
                data-testid="profile-save-button"
              >
                {loading ? "保存中…" : "保存全部"}
              </button>
            </div>
          </div>
        )}

        {saveError && (
          <p className="profile-error" data-testid="profile-error">
            {saveError}
          </p>
        )}

        {DIMENSIONS.map((dim) => (
          <section className="dim" key={dim.key}>
            <h2>
              <span className="dot" />
              {dim.title}
            </h2>
            {editing ? (
              <div className="editor-shell">
                <textarea
                  data-testid={`profile-dim-${dim.key}`}
                  value={draft[dim.key]}
                  onChange={(e) => updateDim(dim.key, e.target.value)}
                />
              </div>
            ) : (
              <div className="body">
                {profile[dim.key].trim() ? (
                  <p>{profile[dim.key]}</p>
                ) : (
                  <p className="muted">（未填写）</p>
                )}
              </div>
            )}
          </section>
        ))}
      </div>
      <SuggestionsModal open={modalOpen} onClose={() => setModalOpen(false)} />
    </div>
  );
}
