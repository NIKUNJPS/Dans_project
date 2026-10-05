import axios from "axios";
import { tokenStore } from "./auth";

const BASE = process.env.REACT_APP_BACKEND_URL;

export const api = axios.create({
    baseURL: BASE,
    headers: { "Content-Type": "application/json" },
});

api.interceptors.request.use((config) => {
    const token = tokenStore.getAccess();
    if (token) {
        config.headers = config.headers || {};
        config.headers.Authorization = `Bearer ${token}`;
    }
    return config;
});

let refreshing = null;
api.interceptors.response.use(
    (r) => r,
    async (error) => {
        const original = error.config;
        if (
            error?.response?.status === 401 &&
            !original._retry &&
            tokenStore.getRefresh() &&
            !original.url?.includes("/auth/refresh")
        ) {
            original._retry = true;
            try {
                if (!refreshing) {
                    refreshing = axios.post(`${BASE}/api/auth/refresh`, {
                        refresh_token: tokenStore.getRefresh(),
                    });
                }
                const resp = await refreshing;
                refreshing = null;
                tokenStore.setTokens(
                    resp.data.access_token,
                    resp.data.refresh_token,
                );
                original.headers.Authorization = `Bearer ${resp.data.access_token}`;
                return api(original);
            } catch (e) {
                refreshing = null;
                tokenStore.clear();
                if (typeof window !== "undefined") {
                    window.location.href = "/login";
                }
            }
        }
        return Promise.reject(error);
    },
);

export const errMessage = (e) =>
    e?.response?.data?.detail ||
    e?.message ||
    "Something went wrong. Please try again.";

/**
 * Download a file from an authenticated endpoint. Goes through the axios client, so an
 * expired access token is refreshed automatically, and shows the server's error text.
 */
export async function downloadFile(path, filename) {
    let resp;
    try {
        resp = await api.get(path, { responseType: "blob", timeout: 5 * 60 * 1000 });
    } catch (e) {
        const data = e?.response?.data;
        if (data instanceof Blob) {
            let detail = "";
            try {
                detail = JSON.parse(await data.text())?.detail || "";
            } catch {
                /* not a JSON error body */
            }
            if (detail) throw new Error(detail);
        }
        throw e;
    }
    const url = URL.createObjectURL(resp.data);
    const a = document.createElement("a");
    a.href = url;
    a.download = filename;
    document.body.appendChild(a);
    a.click();
    a.remove();
    // Revoking immediately can cancel the download in some browsers.
    setTimeout(() => URL.revokeObjectURL(url), 30000);
}
