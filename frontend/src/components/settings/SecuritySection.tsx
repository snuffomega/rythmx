import { useState, useEffect } from 'react';
import { Key, Eye, EyeOff, Copy, Loader2, RefreshCw, CheckCircle, Smartphone } from 'lucide-react';
import { settingsApi, setApiKey } from '../../services/api';
import type { MobilePairing } from '../../types';

interface SecuritySectionProps {
  toast: { success: (m: string) => void; error: (m: string) => void };
}

export function SecuritySection({ toast }: SecuritySectionProps) {
  const [apiKey, setApiKeyState] = useState<string | null>(null);
  const [apiKeyVisible, setApiKeyVisible] = useState(false);
  const [regenerating, setRegenerating] = useState(false);
  const [copied, setCopied] = useState(false);
  const [pairing, setPairing] = useState<MobilePairing | null>(null);
  const [pairingKeyCopied, setPairingKeyCopied] = useState(false);
  const [pairingUrlCopied, setPairingUrlCopied] = useState(false);

  useEffect(() => {
    settingsApi.getApiKey().then(setApiKeyState).catch(() => {});
    settingsApi.getMobilePairing().then(setPairing).catch(() => {});
  }, []);

  const fallbackCopy = (text: string) => {
    const el = document.createElement('textarea');
    el.value = text;
    el.style.position = 'fixed';
    el.style.opacity = '0';
    document.body.appendChild(el);
    el.focus();
    el.select();
    try {
      document.execCommand('copy');
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } finally {
      document.body.removeChild(el);
    }
  };

  const handleCopyApiKey = () => {
    if (!apiKey) return;
    if (navigator.clipboard?.writeText) {
      navigator.clipboard.writeText(apiKey).then(() => {
        setCopied(true);
        setTimeout(() => setCopied(false), 2000);
      }).catch(() => {
        fallbackCopy(apiKey);
      });
    } else {
      fallbackCopy(apiKey);
    }
  };

  const handleRegenerateApiKey = async () => {
    setRegenerating(true);
    try {
      const newKey = await settingsApi.regenerateApiKey();
      setApiKeyState(newKey);
      setApiKey(newKey);
      // Refresh pairing data so the displayed key stays in sync
      settingsApi.getMobilePairing().then(setPairing).catch(() => {});
      toast.success('API key regenerated');
    } catch {
      toast.error('Failed to regenerate API key');
    } finally {
      setRegenerating(false);
    }
  };

  const copyText = (text: string, setCopied: (v: boolean) => void) => {
    const write = navigator.clipboard?.writeText(text);
    if (write) {
      write.then(() => { setCopied(true); setTimeout(() => setCopied(false), 2000); }).catch(() => fallbackCopy(text));
    } else {
      fallbackCopy(text);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    }
  };

  return (
    <section className="border-t border-border-subtle pt-8">
      <h2 className="text-text-muted text-xs font-semibold uppercase tracking-widest mb-4">Security</h2>
      <div className="bg-base border border-border-subtle p-4 space-y-3">
        <div className="flex items-center gap-2.5">
          <div className="w-7 h-7 bg-surface-highlight flex items-center justify-center flex-shrink-0">
            <Key size={14} className="text-text-muted" />
          </div>
          <div>
            <p className="text-text-primary text-sm font-medium">API Key</p>
            <p className="text-text-dim text-[10px]">Include as X-Api-Key header for external integrations</p>
          </div>
        </div>

        <div className="flex items-center gap-2">
          <div className="flex-1 min-w-0 bg-surface-skeleton border border-border px-3 py-2 font-mono text-xs text-text-muted truncate">
            {apiKey
              ? (apiKeyVisible ? apiKey : '•'.repeat(24))
              : <span className="text-text-faint">Loading…</span>}
          </div>
          <button
            onClick={() => setApiKeyVisible(v => !v)}
            className="btn-secondary p-2 flex-shrink-0"
            title={apiKeyVisible ? 'Hide key' : 'Show key'}
          >
            {apiKeyVisible ? <EyeOff size={14} /> : <Eye size={14} />}
          </button>
          <button
            onClick={handleCopyApiKey}
            disabled={!apiKey}
            className="btn-secondary p-2 flex-shrink-0"
            title="Copy to clipboard"
          >
            {copied ? <CheckCircle size={14} className="text-success" /> : <Copy size={14} />}
          </button>
        </div>

        <div className="flex justify-end">
          <button
            onClick={() => void handleRegenerateApiKey()}
            disabled={regenerating}
            className="btn-secondary text-xs flex items-center gap-1.5"
          >
            {regenerating ? <Loader2 size={12} className="animate-spin" /> : <RefreshCw size={12} />}
            Regenerate
          </button>
        </div>
      </div>

      {/* Mobile pairing */}
      <div className="bg-base border border-border-subtle p-4 space-y-3 mt-3">
        <div className="flex items-center gap-2.5">
          <div className="w-7 h-7 bg-surface-highlight flex items-center justify-center flex-shrink-0">
            <Smartphone size={14} className="text-text-muted" />
          </div>
          <div>
            <p className="text-text-primary text-sm font-medium">Mobile Pairing</p>
            <p className="text-text-dim text-[10px]">Enter these values in the Rythmx mobile app settings</p>
          </div>
        </div>

        <div className="space-y-2">
          <p className="text-text-muted text-[10px] uppercase tracking-widest">Server URL</p>
          <div className="flex items-center gap-2">
            <div className="flex-1 min-w-0 bg-surface-skeleton border border-border px-3 py-2 font-mono text-xs text-text-muted truncate">
              {pairing ? pairing.api_base : <span className="text-text-faint">Loading…</span>}
            </div>
            <button
              onClick={() => pairing && copyText(pairing.api_base, setPairingUrlCopied)}
              disabled={!pairing}
              className="btn-secondary p-2 flex-shrink-0"
              title="Copy server URL"
            >
              {pairingUrlCopied ? <CheckCircle size={14} className="text-success" /> : <Copy size={14} />}
            </button>
          </div>

          <p className="text-text-muted text-[10px] uppercase tracking-widest">API Key</p>
          <div className="flex items-center gap-2">
            <div className="flex-1 min-w-0 bg-surface-skeleton border border-border px-3 py-2 font-mono text-xs text-text-muted truncate">
              {pairing ? pairing.api_key : <span className="text-text-faint">Loading…</span>}
            </div>
            <button
              onClick={() => pairing && copyText(pairing.api_key, setPairingKeyCopied)}
              disabled={!pairing}
              className="btn-secondary p-2 flex-shrink-0"
              title="Copy API key"
            >
              {pairingKeyCopied ? <CheckCircle size={14} className="text-success" /> : <Copy size={14} />}
            </button>
          </div>
        </div>
      </div>
    </section>
  );
}
