from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("core", "0002_roles")]

    operations = [
        migrations.CreateModel(
            name="Setting",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False,
                                           verbose_name="ID")),
                ("key", models.CharField(max_length=50, unique=True)),
                ("value", models.CharField(max_length=200)),
            ],
        ),
    ]
