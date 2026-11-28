/// <reference types="vite/client" />

import "react";

declare module "react" {
  // Enable declarative HTML `<dialog closedby="any" | "closerequest" | "none">`
  // per HTML standard while keeping Safari light-dismiss JS fallback in place.
  interface DialogHTMLAttributes<T> extends HTMLAttributes<T> {
    closedby?: "any" | "closerequest" | "none";
  }
}
