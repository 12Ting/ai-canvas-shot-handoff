import type { components } from "../../api/generated-schema";

export type ModelProfile = components["schemas"]["ModelProfileResponse"];
export type ModelProfileRevision = components["schemas"]["ModelProfileRevisionResponse"];
export type ModelProfileRevisionInput = components["schemas"]["ModelProfileRevisionInput"];

export type ModelProfileDeclaration = Readonly<{
  profileKey: string;
  revision: ModelProfileRevisionInput;
}>;
