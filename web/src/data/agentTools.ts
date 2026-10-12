import type { IconName } from '../components/Icon';

/**
 * The read-only tools catalogue shown in "כלי הסוכן". These are the tools the
 * backend agent actually has (the registry seeds in registry/seeds/ plus the
 * built-in area tool). The backend has no route that lists tools, so when a
 * tool is seeded or removed there, update this list too.
 */

export type ToolGroup = 'search' | 'geometry' | 'data';

export interface AgentTool {
  /** Registry source_id, or the built-in tool's name. */
  id: string;
  group: ToolGroup;
  name: string;
  description: string;
  icon: IconName;
}

export const GROUP_ORDER: ToolGroup[] = ['search', 'geometry', 'data'];

export const AGENT_TOOLS: AgentTool[] = [
  {
    id: '9101',
    group: 'search',
    name: 'חיפוש מקומות',
    description:
      'מוצא מקומות בתוך האזור שנבחר במפה, כמו מסעדות, בתי קפה, חנויות ואטרקציות, עם סינון לפי דירוג, מחיר ופתוח עכשיו.',
    icon: 'search',
  },
  {
    id: '9102',
    group: 'search',
    name: 'פרטי מקום',
    description: 'מביא פרטים על מקום מתוצאות החיפוש: טלפון, אתר, שעות פתיחה ותיאור קצר.',
    icon: 'info',
  },
  {
    id: 'geo_describe_area',
    group: 'geometry',
    name: 'תיאור האזור',
    description: 'מסכם את האזור שנבחר: הצורה, השטח והגבולות שלו.',
    icon: 'polygon',
  },
];
