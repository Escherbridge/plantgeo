/**
 * Public operator facts for plantgeo.aevani.com. The governing-law sentence
 * is the entire clause: do not add another venue, a waiver, or a fee.
 */
export const OPERATOR_LEGAL_NAME = "Escherbridge, LLC";
export const OPERATOR_CONTACT_NAME = "Ahmed Zaher";
export const OPERATOR_EMAIL = "contact@escherbridge.com";
export const OPERATOR_PHONE_DISPLAY = "(509) 578-4603";
export const OPERATOR_PHONE_TEL = "+15095784603";
/** City and state only. */
export const OPERATOR_LOCATION = "Boise, Idaho";
export const OPERATOR_LINE = `${OPERATOR_LOCATION}. ${OPERATOR_EMAIL}`;
export const OPERATOR_POSTAL_ADDRESS = {
  "@type": "PostalAddress",
  addressLocality: "Boise",
  addressRegion: "ID",
  addressCountry: "US",
} as const;

export const TERMS_GOVERNING_LAW =
  "These terms are governed by the laws of the State of Idaho, and any dispute will be resolved in the state or federal courts located in Ada County, Idaho.";
