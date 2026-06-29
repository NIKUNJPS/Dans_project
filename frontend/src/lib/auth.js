const ACCESS_KEY = "sm.access";
const REFRESH_KEY = "sm.refresh";
const USER_KEY = "sm.user";

export const tokenStore = {
    getAccess: () => {
        if (typeof window === "undefined") return null;
        return window.localStorage.getItem(ACCESS_KEY);
    },
    getRefresh: () => {
        if (typeof window === "undefined") return null;
        return window.localStorage.getItem(REFRESH_KEY);
    },
    getUser: () => {
        if (typeof window === "undefined") return null;
        const raw = window.localStorage.getItem(USER_KEY);
        try {
            return raw ? JSON.parse(raw) : null;
        } catch {
            return null;
        }
    },
    setTokens: (access, refresh) => {
        window.localStorage.setItem(ACCESS_KEY, access);
        if (refresh) window.localStorage.setItem(REFRESH_KEY, refresh);
    },
    setUser: (user) => {
        window.localStorage.setItem(USER_KEY, JSON.stringify(user));
    },
    clear: () => {
        window.localStorage.removeItem(ACCESS_KEY);
        window.localStorage.removeItem(REFRESH_KEY);
        window.localStorage.removeItem(USER_KEY);
    },
};
