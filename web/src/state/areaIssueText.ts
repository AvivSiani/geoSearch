import type { AreaIssue } from '../geo/measure';
import { he } from '../i18n/he';

export function areaIssueText(issue: AreaIssue): string {
  switch (issue.kind) {
    case 'too_few_points':
      return he.area.tooFewPoints;
    case 'self_intersects':
      return he.area.selfIntersects;
    case 'too_large':
      return he.area.tooLarge(issue.km2, issue.maxKm2);
  }
}
