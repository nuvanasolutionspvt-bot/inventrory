# Contact form Google Sheets setup

1. Open the supplied Google Sheet, then Extensions > Apps Script.
2. Paste Code.gs into the script editor and save.
3. In Project Settings > Script properties, add CONTACT_TOKEN with a long random secret. Generate one with `python -c "import secrets; print(secrets.token_urlsafe(32))"`. Keep it private.
4. Deploy > New deployment > Web app. Execute as: Me. Who has access: Anyone. Authorize and copy the URL ending in /exec (not /dev).
5. Set these environment variables for the Django application on AWS:

   CONTACT_APPS_SCRIPT_URL=https://script.google.com/macros/s/YOUR_DEPLOYMENT_ID/exec
   CONTACT_APPS_SCRIPT_TOKEN=the_same_secret_from_step_3

6. Deploy this code and restart the Django application service so it loads the environment variables.
7. Submit the Contact Us form. A new Contact Enquiries tab will be created in the specified sheet; existing tabs are not modified. Success is displayed only after Apps Script acknowledges saving.

Django loads pos_saas/.env automatically using python-dotenv. Install requirements after pulling updates. Existing service environment variables take precedence; remove old CONTACT_APPS_SCRIPT_* Environment entries from the systemd override when switching to .env. Restart inventory.service after changing .env. Never put the token into the template or commit it to GitHub.

If access is restricted by your Google Workspace administrator, Anyone may be unavailable; ask the administrator to allow this deployment method.

After editing Apps Script, update the deployment to a new version. See https://developers.google.com/apps-script/guides/web .
