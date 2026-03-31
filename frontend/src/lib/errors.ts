import { AxiosError } from "axios";

export function getErrorMessage(error: unknown): string {
  if (error instanceof AxiosError) {
    const data = error.response?.data;
    if (typeof data === "string") return data;
    if (data?.detail) {
      // FastAPI validation errors return detail as an array of {msg, loc, type, input}
      if (Array.isArray(data.detail)) {
        return data.detail.map((e: { msg?: string }) => e.msg ?? String(e)).join("; ");
      }
      if (typeof data.detail === "string") return data.detail;
      return String(data.detail);
    }
    if (data?.message) return data.message;
    if (error.message) return error.message;
  }
  if (error instanceof Error) return error.message;
  return "An unexpected error occurred";
}
