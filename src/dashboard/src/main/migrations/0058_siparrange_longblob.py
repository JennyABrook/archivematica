# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from django.db import migrations
import main.models


class Migration(migrations.Migration):

    dependencies = [
        ('main', '0057_7zip_no_compression'),
    ]

    operations = [
        migrations.AlterField(
            model_name='siparrange',
            name='arrange_path',
            field=main.models.BlobTextField(),
        ),
        migrations.AlterField(
            model_name='siparrange',
            name='original_path',
            field=main.models.BlobTextField(default=None, null=True, blank=True),
        ),
    ]
