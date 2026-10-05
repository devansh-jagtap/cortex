"use client";

import { useState } from "react";

import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogTitle } from "@/components/ui/dialog";

/**
 * Outside the desktop app there is no native folder picker, so the folder's
 * path is typed or pasted instead. The engine checks that it is a folder.
 */
export function AddFolderDialog({
  open,
  onOpenChange,
  onAdd,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onAdd: (path: string) => Promise<string | null>;
}) {
  const [path, setPath] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function submit() {
    if (!path.trim()) return;
    setBusy(true);
    setError(null);
    const problem = await onAdd(path.trim());
    setBusy(false);
    if (problem) setError(problem);
    else {
      setPath("");
      onOpenChange(false);
    }
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-md">
        <DialogTitle className="font-serif text-2xl font-normal">Add a folder</DialogTitle>
        <DialogDescription>
          Paste the path of a folder with photos. Cortex indexes everything inside it and keeps watching for new
          photos. In the desktop app this opens a folder picker instead.
        </DialogDescription>
        <form
          className="flex flex-col gap-3"
          onSubmit={(e) => {
            e.preventDefault();
            void submit();
          }}
        >
          <input
            autoFocus
            value={path}
            onChange={(e) => setPath(e.target.value)}
            placeholder="C:\Users\you\Pictures"
            aria-label="Folder path"
            spellCheck={false}
            className="rounded-lg border border-input bg-background px-3 py-2 text-sm outline-none placeholder:text-muted-foreground/60 focus-visible:border-star"
          />
          {error && <p className="text-sm text-destructive">{error}</p>}
          <div className="flex justify-end gap-2">
            <Button type="button" variant="ghost" onClick={() => onOpenChange(false)}>
              Cancel
            </Button>
            <Button type="submit" disabled={busy || !path.trim()}>
              {busy ? "Adding…" : "Add folder"}
            </Button>
          </div>
        </form>
      </DialogContent>
    </Dialog>
  );
}
