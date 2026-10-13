// Device signing keys. Where the browser supports WebCrypto Ed25519, the user's mandate and cart approvals are
// signed on this device with a non-extractable private key kept in IndexedDB; the server only ever sees the
// public key. Otherwise the server's clearly-labelled custodial demo key is used.
import { api } from "./api";

const DB = "leash-keys";
const STORE = "keys";

type Stored = { kid: string; pair: CryptoKeyPair };

function idb<T>(mode: IDBTransactionMode, fn: (s: IDBObjectStore) => IDBRequest<T>): Promise<T> {
  return new Promise((resolve, reject) => {
    const open = indexedDB.open(DB, 1);
    open.onupgradeneeded = () => open.result.createObjectStore(STORE);
    open.onerror = () => reject(open.error);
    open.onsuccess = () => {
      const tx = open.result.transaction(STORE, mode);
      const req = fn(tx.objectStore(STORE));
      req.onsuccess = () => resolve(req.result);
      req.onerror = () => reject(req.error);
    };
  });
}

function b64u(bytes: ArrayBuffer | Uint8Array): string {
  const arr = bytes instanceof Uint8Array ? bytes : new Uint8Array(bytes);
  let s = "";
  arr.forEach((b) => (s += String.fromCharCode(b)));
  return btoa(s).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

const enc = (o: unknown) => b64u(new TextEncoder().encode(JSON.stringify(o)));

let cached: Promise<Stored | null> | null = null;

export async function supportsEd25519(): Promise<boolean> {
  try {
    if (!crypto?.subtle) return false;
    await crypto.subtle.generateKey({ name: "Ed25519" } as AlgorithmIdentifier, false, ["sign", "verify"]);
    return true;
  } catch {
    return false;
  }
}

/** Load this device's key, creating and registering it on first use. Null when unsupported. */
export function deviceKey(): Promise<Stored | null> {
  if (!cached) {
    cached = (async () => {
      try {
        const existing = await idb<Stored | undefined>("readonly", (s) => s.get("device"));
        if (existing?.kid) return existing;
        if (!(await supportsEd25519())) return null;
        const pair = (await crypto.subtle.generateKey({ name: "Ed25519" } as AlgorithmIdentifier, false, ["sign", "verify"])) as CryptoKeyPair;
        const raw = await crypto.subtle.exportKey("raw", pair.publicKey);
        const { kid } = await api.registerKey({ kty: "OKP", crv: "Ed25519", x: b64u(raw) }, navigator.userAgent.split(" ").slice(-1)[0] || "browser");
        const stored = { kid, pair };
        await idb("readwrite", (s) => s.put(stored, "device"));
        return stored;
      } catch {
        return null;
      }
    })();
  }
  return cached;
}

/** Compact JWS (EdDSA) over the payload, or null if this device cannot sign. */
export async function signJws(payload: Record<string, unknown>, typ: string): Promise<string | null> {
  const key = await deviceKey();
  if (!key) return null;
  const input = `${enc({ alg: "EdDSA", typ, kid: key.kid })}.${enc(payload)}`;
  const sig = await crypto.subtle.sign({ name: "Ed25519" } as AlgorithmIdentifier, key.pair.privateKey, new TextEncoder().encode(input));
  return `${input}.${b64u(sig)}`;
}
