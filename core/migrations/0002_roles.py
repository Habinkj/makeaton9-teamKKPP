from django.db import migrations

ROLES = ["admin", "staff", "customer"]


def create_roles(apps, schema_editor):
    Group = apps.get_model("auth", "Group")
    for name in ROLES:
        Group.objects.get_or_create(name=name)


class Migration(migrations.Migration):
    dependencies = [("core", "0001_initial")]
    operations = [migrations.RunPython(create_roles, migrations.RunPython.noop)]
