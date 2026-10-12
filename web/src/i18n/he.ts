/**
 * Every user-facing string. The first block is the design handoff's copy
 * (README §7), used verbatim; the rest covers states the handoff doesn't name
 * (errors, notices, result details).
 */

const numberFormat = new Intl.NumberFormat('he-IL', { maximumFractionDigits: 1 });

/** "2.4" for km²: one decimal, but small areas keep two significant digits. */
export function formatKm2(km2: number): string {
  if (km2 > 0 && km2 < 0.1) {
    return new Intl.NumberFormat('he-IL', { maximumSignificantDigits: 2 }).format(km2);
  }
  return numberFormat.format(km2);
}

export const he = {
  app: {
    name: 'חיפוש',
    nameAccent: 'גאוגרפי',
    project: 'הפרויקט שלי',
  },
  account: { aria: 'חשבון' },
  theme: {
    toDark: 'מעבר למצב כהה',
    toLight: 'מעבר למצב בהיר',
  },
  history: {
    title: 'היסטוריה',
    count: (n: number) => (n === 1 ? 'חיפוש אחד' : `${n} חיפושים`),
    search: 'חיפוש בהיסטוריה',
    results: (n: number) => (n === 1 ? 'תוצאה אחת' : `${n} תוצאות`),
    empty: 'אין חיפושים תואמים.',
    none: 'עוד אין חיפושים. השאלות שתשאלו יופיעו כאן.',
    today: 'היום',
    yesterday: 'אתמול',
    listAria: 'חיפושים קודמים',
    expired: 'פג תוקף',
  },
  map: {
    aria: 'מפה',
    search: 'חיפוש מקום או קואורדינטות',
    searchNoResults: 'לא נמצאו מקומות.',
    searchError: 'החיפוש נכשל. נסו שוב.',
    searching: 'מחפש…',
    expand: 'הגדלת מפה',
    collapse: 'הקטנת מפה',
    basemap: 'מפת בסיס',
    basemapCurrent: (label: string) => `מפת בסיס: ${label}`,
    zoomIn: 'התקרבות',
    zoomOut: 'התרחקות',
    coordsAria: 'מיקום הסמן',
    tileError: 'חלק מאריחי המפה לא נטענו.',
  },
  draw: {
    toolbarAria: 'בחירת אזור',
    polygon: 'מצולע',
    rect: 'מלבן',
    circle: 'עיגול',
    free: 'ציור חופשי',
    edit: 'עריכה',
    clear: 'ניקוי',
    areaLabel: (n: number, km2: number) => `מצולע ${n} · ${formatKm2(km2)} קמ״ר`,
    tooltips: {
      firstVertex: 'לחצו כדי להתחיל לצייר',
      continueLine: 'לחצו כדי להוסיף נקודה',
      finishPoly: 'לחצו על הנקודה הראשונה כדי לסגור',
      finishRect: 'לחצו כדי לסיים',
      startCircle: 'לחצו כדי לקבוע את המרכז',
      finishCircle: 'לחצו כדי לקבוע את הרדיוס',
      freehand: 'גררו כדי לצייר',
    },
  },
  area: {
    tooLarge: (km2: number, max: number) =>
      `האזור גדול מדי (${formatKm2(km2)} קמ״ר). המקסימום הוא ${formatKm2(max)} קמ״ר.`,
    selfIntersects: 'הצורה חותכת את עצמה. ציירו אותה מחדש בלי קווים חוצים.',
    tooFewPoints: 'צריך לפחות שלוש נקודות כדי ליצור אזור.',
  },
  chat: {
    aria: 'שיחה עם הסוכן',
    title: 'הסוכן הגאוגרפי',
    status: (n: number) => `מוכן · ${n} כלים זמינים`,
    statusWorking: 'עובד…',
    statusOffline: 'לא מחובר',
    toolsToggle: (n: number) => `כלי הסוכן · ${n}`,
    new: 'שיחה חדשה',
    greeting: 'היי! ציירו או בחרו אזור במפה, ואז שאלו אותי כל דבר על מה שנמצא בתוכו.',
    working: 'עובד על זה…',
    workingStep: 'רץ על האזור שנבחר',
    inputLabel: 'הודעה לסוכן',
    placeholder: 'שאלו על האזור שנבחר, למשל: אילו בניינים כאן גבוהים מ-10 קומות?',
    placeholderNoArea: 'ציירו אזור במפה כדי לשאול עליו.',
    areaChip: (n: number, km2: number) => `מצולע ${n} · ${formatKm2(km2)} קמ״ר`,
    areaChipRemove: 'הסרת האזור מהשאלה',
    sendHint: 'Enter לשליחה',
    sendAria: 'שליחה',
    promptTooLong: (len: number, max: number) => `ההודעה ארוכה מדי (${len}/${max} תווים).`,
    needArea: 'כדי לשאול, ציירו קודם אזור במפה.',
    areaChanged: 'האזור השתנה, אז התחלנו שיחה חדשה. השיחה הקודמת שמורה בהיסטוריה.',
    expiredContinue:
      'השיחה הזו פגה בשרת. השאלה הבאה תתחיל שיחה חדשה על אותו אזור, והסוכן לא יזכור את מה שנאמר קודם.',
    retry: 'ניסיון חוזר',
    interrupted: 'הבקשה נקטעה לפני שהתקבלה תשובה.',
    callLimit: 'הסוכן הגיע למגבלת הצעדים לפני שסיים. התשובה עשויה להיות חלקית.',
    agentAvatar: 'הסוכן',
    youSaid: 'אתם',
    examplesAria: 'שאלות לדוגמה',
    examples: ['מסעדות אסייתיות לא יקרות באזור', 'בתי קפה עם דירוג גבוה', 'מה יש באזור הזה?'],
  },
  results: {
    aria: 'תוצאות',
    showing: (shown: number, total: number) => `מוצגים ${shown} מתוך ${total}`,
    export: 'ייצוא',
    exportAria: 'ייצוא התוצאות כקובץ GeoJSON',
    all: (total: number) => `הצגת כל ה-${total}`,
    less: 'הצגת פחות',
    rating: (r: number, count?: number) =>
      count ? `דירוג ${r} (${count.toLocaleString('he-IL')})` : `דירוג ${r}`,
    untitled: (n: number) => `תוצאה ${n}`,
    website: 'אתר',
    phone: 'טלפון',
    citeAria: (n: number) => `תוצאה ${n}`,
    price: {
      PRICE_LEVEL_FREE: 'חינם',
      PRICE_LEVEL_INEXPENSIVE: '₪',
      PRICE_LEVEL_MODERATE: '₪₪',
      PRICE_LEVEL_EXPENSIVE: '₪₪₪',
      PRICE_LEVEL_VERY_EXPENSIVE: '₪₪₪₪',
    } as Record<string, string>,
  },
  tools: {
    title: 'כלי הסוכן',
    explainer: 'הכלים שהסוכן משתמש בהם כדי לענות. העבירו את העכבר על כלי כדי לראות מה הוא עושה.',
    close: 'סגירת כלי הסוכן',
    group: {
      search: 'חיפוש',
      geometry: 'גאומטריה',
      data: 'נתונים ותצלומים',
    },
  },
  errors: {
    network: 'אין חיבור לשרת. בדקו שהשרת פועל ונסו שוב.',
    timeout: 'הסוכן לא ענה בזמן. נסו שוב או נסחו את השאלה בקצרה.',
    badResponse: 'התקבלה תשובה לא צפויה מהשרת.',
    unknown: 'משהו השתבש. נסו שוב.',
    MODEL_UNAVAILABLE: 'מודל השפה לא זמין כרגע. נסו שוב בעוד כמה רגעים.',
    STORE_UNAVAILABLE: 'מאגר השיחות לא זמין כרגע. נסו שוב בעוד כמה רגעים.',
    CONVERSATION_BUSY: 'הסוכן עדיין עובד על השאלה הקודמת בשיחה הזו. נסו שוב בעוד רגע.',
    CONVERSATION_LIMIT: (max: unknown) =>
      `השיחה הגיעה למגבלה של ${typeof max === 'number' ? max : 20} שאלות. התחילו שיחה חדשה כדי להמשיך.`,
    CONVERSATION_NOT_FOUND: 'השיחה לא נמצאה בשרת (ייתכן שפג תוקפה).',
    AREA_MISMATCH: 'אי אפשר לשנות את האזור באמצע שיחה. התחילו שיחה חדשה.',
    AREA_TOO_LARGE: 'האזור גדול מדי. ציירו אזור קטן יותר.',
    INVALID_GEOMETRY: 'הצורה שציירתם לא תקינה (למשל קווים שחוצים זה את זה). ציירו אותה מחדש.',
    WKT_TOO_LARGE: 'לצורה יש יותר מדי נקודות. ציירו צורה פשוטה יותר.',
    WKT_PARSE_ERROR: 'לא הצלחנו לקרוא את הצורה. ציירו אותה מחדש.',
    UNSUPPORTED_GEOMETRY: 'סוג הצורה הזה לא נתמך.',
    OUT_OF_RANGE: 'האזור יוצא מגבולות המפה הנתמכים.',
    PROMPT_TOO_LONG: 'ההודעה ארוכה מדי. קצרו אותה ונסו שוב.',
    MISSING_PROMPT: 'כתבו שאלה לפני השליחה.',
    MISSING_WKT: 'כדי לשאול, ציירו קודם אזור במפה.',
    generic: 'הבקשה נדחתה על ידי השרת.',
  },
} as const;
