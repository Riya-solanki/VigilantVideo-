# Goal Description

Implement a 3-day expiration policy for protected videos. Once a video is processed (`completed_at`), it will only be available for download for 3 days. After 3 days, the video file will be permanently deleted from Cloudflare R2 to save storage, and the UI will show an "Expired" status instead of a Download button.

## User Review Required

Please review the plan below. We will use a "lazy evaluation" approach: the expiration check will happen automatically whenever the user opens their dashboard. If 3 days have passed, the file is deleted from R2 and marked as expired in the database.

## Open Questions

None.

## Proposed Changes

### Backend Logic
#### [MODIFY] [app.py](file:///c:/Users/hp/OneDrive%20-%20Amrita%20vishwa%20vidyapeetham/Documents/SEM%20VI/Software_Engineering/New%20folder/VigilantVideo-/app.py)
- Import `timedelta` from `datetime`.
- In `/api/dashboard`, before preparing the response data, iterate through the user's jobs.
- If a job is `status == 'done'`, has a `completed_at` timestamp, and `datetime.utcnow() - j.completed_at > timedelta(days=3)`, then:
  - Connect to R2 and delete the object at `j.output_path`.
  - Update `j.status = 'expired'`.
  - Call `db.session.commit()` to save these state changes.
- Update `_job_to_video` to pass the `expired` status to the frontend.

### Frontend UI
#### [MODIFY] [userDashboard.js](file:///c:/Users/hp/OneDrive%20-%20Amrita%20vishwa%20vidyapeetham/Documents/SEM%20VI/Software_Engineering/New%20folder/VigilantVideo-/static/js/userDashboard.js)
- In the `renderTable()` function, add a condition to handle `v.status === 'expired'`.
- When expired, set the status badge to "Expired".
- Remove the "Download" button from the Actions column, leaving only the "Delete" button so the user can clear the record from their library.

## Verification Plan

### Automated Tests
- N/A

### Manual Verification
- Manually change a video's `completed_at` timestamp in the database to be older than 3 days.
- Refresh the dashboard to confirm the status changes to "Expired", the "Download" button disappears, and no errors occur.
