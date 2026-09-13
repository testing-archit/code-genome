import React, { useMemo as memo } from "react";
import { formatName } from "./format";

export interface Profile {
  name: string;
}

export class ProfileCard {
  render(profile: Profile): string {
    return formatName(profile.name);
  }
}

export const buildProfile = (name: string): Profile => ({ name });

export default function ProfilePage() {
  return <main>{memo(() => "Profile", [])}</main>;
}

