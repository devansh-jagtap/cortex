"use client";

import { CalendarIcon, MapPinIcon, TagIcon, XIcon } from "lucide-react";

import { Button } from "@/components/ui/button";
import type { EntityDetail, EntitySummary, EntityType } from "@/lib/backend";
import { plural } from "@/lib/format";
import { cn } from "@/lib/utils";

const ICONS: Record<EntityType, typeof MapPinIcon> = {
  place: MapPinIcon,
  event: CalendarIcon,
  scene: TagIcon,
  person: TagIcon,
};

function shortDate(unixSeconds: number) {
  return new Date(unixSeconds * 1000).toLocaleDateString(undefined, { month: "short", day: "numeric", year: "numeric" });
}

/** One line saying what an entity is, in plain words. */
export function describeEntity(entity: EntitySummary): string {
  const d = entity.data ?? {};
  if (entity.type === "place") {
    const within = [d.region !== entity.name ? d.region : null, d.country].filter(Boolean).join(", ");
    if (entity.level === "city") return within ? `City in ${within}` : "City";
    if (entity.level === "region") return d.country ? `Region in ${d.country}` : "Region";
    return "Country";
  }
  if (entity.type === "event" && d.start) {
    const span =
      d.end && shortDate(d.end) !== shortDate(d.start) ? `${shortDate(d.start)} to ${shortDate(d.end)}` : shortDate(d.start);
    return d.trip ? `Trip, ${span}` : `Event, ${span}`;
  }
  if (entity.type === "scene") return "What's in the photo";
  return "";
}

export function EntityChip({ entity, onOpen }: { entity: EntitySummary; onOpen: (id: number) => void }) {
  const Icon = ICONS[entity.type];
  return (
    <button
      type="button"
      onClick={() => onOpen(entity.id)}
      title={describeEntity(entity)}
      className="inline-flex max-w-full items-center gap-1.5 rounded-full border border-border px-2.5 py-1 text-xs text-foreground/90 transition-colors hover:border-star/60 hover:bg-accent focus-visible:outline-2 focus-visible:outline-star"
    >
      <Icon className={cn("size-3 shrink-0", entity.type === "place" ? "text-star" : "text-muted-foreground")} />
      <span className="truncate">{entity.name}</span>
    </button>
  );
}

/** Shown above the grid when browsing one place, scene, or event. */
export function EntityHeader({
  entity,
  onOpen,
  onClose,
}: {
  entity: EntityDetail;
  onOpen: (id: number) => void;
  onClose: () => void;
}) {
  const groups: { label: string; items: EntityDetail["related"] }[] = [
    { label: "Places", items: entity.related.filter((r) => r.type === "place") },
    { label: "Events", items: entity.related.filter((r) => r.type === "event") },
    { label: "In the photos", items: entity.related.filter((r) => r.type === "scene") },
  ].filter((g) => g.items.length > 0);

  return (
    <div className="flex flex-col gap-4">
      <div className="flex items-start justify-between gap-4">
        <div>
          <h2 className="font-serif text-[34px] leading-[1.1] tracking-[-0.01em]">{entity.name}</h2>
          <p className="mt-1.5 text-sm text-muted-foreground">
            {describeEntity(entity)}
            <span className="ml-3 text-foreground/90">{plural(entity.photos_total, "photo")}</span>
          </p>
        </div>
        <Button variant="ghost" size="sm" onClick={onClose} aria-label="Back to all photos">
          <XIcon />
          All photos
        </Button>
      </div>
      {groups.length > 0 && (
        <dl className="flex flex-col gap-2.5">
          {groups.map((group) => (
            <div key={group.label} className="flex flex-wrap items-center gap-1.5">
              <dt className="mr-1 w-[104px] shrink-0 text-xs text-muted-foreground">{group.label}</dt>
              {group.items.slice(0, 12).map((r) => (
                <dd key={r.id}>
                  <EntityChip entity={r} onOpen={onOpen} />
                </dd>
              ))}
            </div>
          ))}
        </dl>
      )}
    </div>
  );
}
