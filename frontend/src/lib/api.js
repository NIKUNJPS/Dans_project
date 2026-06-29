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
