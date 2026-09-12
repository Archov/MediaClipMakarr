import { createContext, useContext, useMemo, useState } from "react";
import type { ReactNode } from "react";

interface SettingsSaveStatusValue {
  text: string | null;
  setText: (text: string | null) => void;
}

const SettingsSaveStatusContext = createContext<SettingsSaveStatusValue | null>(null);

/** Lets the Settings form (mounted only on the Settings screen) publish its
 * autosave status text up to the app header, which renders it centered below
 * the nav buttons — without coupling the header to the settings form's own
 * state tree. */
export function SettingsSaveStatusProvider({ children }: { children: ReactNode }) {
  const [text, setText] = useState<string | null>(null);
  const value = useMemo(() => ({ text, setText }), [text]);
  return (
    <SettingsSaveStatusContext.Provider value={value}>
      {children}
    </SettingsSaveStatusContext.Provider>
  );
}

export function useSettingsSaveStatus(): SettingsSaveStatusValue {
  const context = useContext(SettingsSaveStatusContext);
  if (!context) {
    throw new Error("useSettingsSaveStatus must be used within a SettingsSaveStatusProvider");
  }
  return context;
}
