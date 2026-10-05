import type { Metadata } from "next";
import { JobDetails } from "@/components/JobDetails";

export const metadata: Metadata = { title: "Puzzle job" };

export default async function JobPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  return <JobDetails id={id} />;
}
