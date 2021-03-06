import os
import shutil
import stat
import zipfile
from mock import MagicMock, patch

from decimal import Decimal
from datetime import datetime
from django.test import TestCase
from django.db import connection, OperationalError
from django.core import mail

from ratechecker.management.commands.load_daily_data import Command, OaHException
from ratechecker.models import Product, Adjustment, Rate, Region


class LoadDailyTestCase(TestCase):

    def create_test_files(self, data):
        """ Create files in test_folder for tests."""
        for key in data:
            f = file(data[key]['file'], 'w+')
            f.write('So the size is not 0')
            f.close()

    def create_product(self, row):
        """ Helper function to save a product."""
        p = Product()
        p.plan_id = int(row[0])
        p.institution = row[1]
        p.loan_purpose = row[2]
        p.pmt_type = row[3]
        p.loan_type = row[4]
        p.loan_term = int(row[5])
        p.int_adj_term = self.c.nullable_int(row[6])
        p.adj_period = self.c.nullable_int(row[7])
        p.io = self.c.string_to_boolean(row[8])
        p.arm_index = self.c.nullable_string(row[9])
        p.int_adj_cap = self.c.nullable_int(row[10])
        p.annual_cap = self.c.nullable_int(row[11])
        p.loan_cap = self.c.nullable_int(row[12])
        p.arm_margin = self.c.nullable_decimal(row[13])
        p.ai_value = self.c.nullable_decimal(row[14])
        p.min_ltv = float(row[15])
        p.max_ltv = float(row[16])
        p.min_fico = int(row[17])
        p.max_fico = int(row[18])
        p.min_loan_amt = Decimal(row[19])
        p.max_loan_amt = Decimal(row[20])
        p.data_timestamp = datetime.strptime('20140101', '%Y%m%d')
        p.save()
        return p

    def setUp(self):
        self.c = Command()
        self.test_dir = 'ratechecker/tests/test_folder'
        self.dummyargs = {
            'product': {'date': '20140101', 'file': '20140101_product.txt'},
            'adjustment': {'date': '20140101', 'file': '20140101_adjustment.txt'},
            'rate': {'date': '20140101', 'file': '20140101_rate.txt'},
            'region': {'date': '20140101', 'file': '20140101_region.txt'},
        }

        os.mkdir(self.test_dir)
        os.chdir(self.test_dir)

    def tearDown(self):
        """ Delete the test_folder dir."""
        os.chdir('../../..')
        path = os.path.join(os.getcwd(), self.test_dir)
        shutil.rmtree(path)

    def test_string_to_boolean(self):
        b = self.c.string_to_boolean('abc')
        self.assertEqual(b, None)

        b = self.c.string_to_boolean('False')
        self.assertFalse(b)

        b = self.c.string_to_boolean('True')
        self.assertTrue(True)

    def test_nullable_int(self):
        self.assertEqual(self.c.nullable_int('10'), 10)
        self.assertEqual(self.c.nullable_int('10.0'), 10)
        self.assertEqual(self.c.nullable_int(''), None)
        self.assertEqual(self.c.nullable_int(' '), None)

    def test_nullable_string(self):
        self.assertEqual(self.c.nullable_string('BANK'), 'BANK')
        self.assertEqual(self.c.nullable_string(' '), None)
        self.assertEqual(self.c.nullable_string(''), None)

    def test_nullable_decimal(self):
        self.assertEqual(self.c.nullable_decimal(''), None)
        self.assertEqual(self.c.nullable_decimal(' '), None)
        self.assertEqual(self.c.nullable_decimal('12.5'), Decimal('12.5'))

    def test_nullable_float(self):
        self.assertEqual(self.c.nullable_float(''), None)
        self.assertEqual(self.c.nullable_float(' '), None)
        self.assertEqual(self.c.nullable_float('12.5'), 12.5)

    def test_create_rate(self):
        #rate_id, product_id, region_id, lock, base_rate, total_points
        row = ['1', '12', '200', '40', '3.125', '3']

        now = datetime.now()
        r = self.c.create_rate(row, now)
        self.assertEqual(r.rate_id, 1)
        self.assertEqual(r.product_id, 12)
        self.assertEqual(r.lock, 40)
        self.assertEqual(r.base_rate, Decimal('3.125'))
        self.assertEqual(r.data_timestamp, now)
        self.assertEqual(r.region_id, 200)

    def test_delete_temp_tables(self):
        """ ...  some exist, others - not."""
        # don't know how to easily test table (in)existence
        cursor = connection.cursor()
        cursor.execute('CREATE TABLE temporary_product(a TEXT)')
        self.assertRaises(OperationalError, cursor.execute, 'SELECT * FROM temporary_region')
        empty = cursor.execute('SELECT * FROM temporary_product')
        self.assertTrue(empty is not None)
        self.c.delete_temp_tables(cursor)
        self.assertRaises(OperationalError, cursor.execute, 'SELECT * FROM temporary_product')

    def test_reload_old_data(self):
        """ .. archive_data_to_temp_tables, delete_temp_tables, delete_data_from_base_tables """
        cursor = connection.cursor()
        row = [
            '5999', 'SMPL', 'PURCH', 'ARM', 'JUMBO', '30', '7.0', '1',
            'False', 'LIBOR', '5.0000', '2.0000', '5.0000', '2.5000',
            '.5532', '1', '90', '620', '850', '417001', '2000000', 1, 1, 0
        ]
        # Original product object
        op = self.create_product(row)

        self.c.archive_data_to_temp_tables(cursor)

        result = cursor.execute('SELECT * FROM temporary_product')
        self.assertTrue(len(result.fetchall()) == 1)
        result = Product.objects.all()
        self.assertTrue(result)
        self.c.delete_data_from_base_tables()
        result = Product.objects.all()
        self.assertFalse(result)

        self.c.reload_old_data(cursor)
        result = Product.objects.all()
        self.assertEqual(len(result), 1)
        self.assertTrue(op == result[0])

    #TODO: add checks for other situations
    def test_handle__no_folder(self):
        """ .. check that some issues are caught."""
        self.c.handle()
        self.assertEqual(1, self.c.status)
        self.assertTrue('Error: tuple index out of range. Has a source directory been provided?'
                in self.c.messages)
        self.assertFalse('Warning: reloading "yesterday" data.' in self.c.messages)

    def test_handle__bad_folder(self):
        """ .. check that some issues are caught."""
        # inexistent folder, inaccessible folder or a file all are caught in the same place
        with open('not_a_folder', 'w') as fake_folder:
            fake_folder.write('I am not a folder')
        self.c.handle('not_a_folder')
        self.assertEqual(1, self.c.status)

    def test_handle__bad_zipfile(self):
        """ .. check that some issues are caught."""
        with open('20140101.zip', 'w') as fake_zip_archive:
            fake_zip_archive.write('Some text')
        self.c.handle('.')
        self.assertEqual(self.c.status, 1)
        self.assertTrue(' Warning: File is not a zip file.' in self.c.messages)
        self.assertTrue('Warning: reloading "yesterday" data' in self.c.messages)

    def test_arch_list(self):
        """ .. only the correct filenames are returned and are sorted."""
        self.create_test_files(self.dummyargs)
        arch_name = '%s.zip' % self.dummyargs['rate']['date']
        zfile = zipfile.ZipFile(arch_name, 'w')
        for key in self.dummyargs:
            zfile.write(self.dummyargs[key]['file'])
        zfile.close()
        shutil.copy(arch_name, '20130202.zip')
        shutil.copy(arch_name, '20150202.zip')
        result = self.c.arch_list('.')
        self.assertEqual(len(result), 3)
        self.assertTrue('20150202' in result[0])
        self.assertTrue(arch_name in result[1])
        self.assertTrue('20130202' in result[2])

    def test_load_arch_data(self):
        """ .. check all load_xxx_data functions here."""
        zfile = self.prepare_sample_data()
        result = self.c.load_arch_data(zfile)
        products = Product.objects.all()
        self.assertEqual(len(products), 3)
        self.assertTrue(products[1].institution, 'SMPL1')
        self.assertTrue(products[0].institution, 'SMPL')
        self.assertTrue(products[2].institution, 'SMPL2')
        self.assertTrue(products[1].loan_purpose, 'REFI')
        self.assertTrue(products[1].pmt_type, 'FIXED')

        #TODO: add other checks
        adjustments = Adjustment.objects.all()
        self.assertEqual(len(adjustments), 4)

        rates = Rate.objects.all()
        self.assertEqual(len(rates), 7)

        regions = Region.objects.all()
        self.assertEqual(len(regions), 4)

    def prepare_sample_data(self):
        """ solely for test_load_arch_data."""
        self.create_test_files(self.dummyargs)
        date = self.dummyargs['product']['date']
        filename = '%s_product.txt' % date
        with open(filename, 'w') as prdata:
            prdata.write("Is skipped anyway\n")
            prdata.write("7487\tSMPL\tPURCH\tARM\tJUMBO\t30\t7.0\t1\tFalse\tLIBOR\t5.0000\t2.0000\t5.0000\t2.5000\t.5532\t1\t90\t620\t850\t417001\t2000000\t1\t1\t0\n")
            prdata.write("7488\tSMPL1\tREFI\tFIXED\tCONF\t30\t7.0\t1\tFalse\tLIBOR\t5.0000\t2.0000\t5.0000\t2.5000\t.5532\t1\t90\t620\t850\t417001\t2000000\t1\t1\t0\n")
            prdata.write("7489\tSMPL2\tREFI\tARM\tJUMBO\t30\t7.0\t1\tFalse\tLIBOR\t5.0000\t2.0000\t5.0000\t2.5000\t.5532\t1\t90\t620\t850\t417001\t2000000\t1\t1\t0\n")

        filename = filename.replace('_product', '_adjustment')
        with open(filename, 'w') as adjdata:
            adjdata.write("Is skipped anyway\n")
            adjdata.write("7487\t67600\tP\t-0.25\t\t\t\t720\t739\t1\t60\t\n")
            adjdata.write("7488\t73779\tP\t0\t850001.000\t1000000.000\t\t\t\t\t\t\n")
            adjdata.write("7489\t68040\tP\t3.25\t\t\t\t620\t639\t85.010000000000005\t95\t\n")
            adjdata.write("7489\t67996\tP\t1.5\t\t\t\t620\t639\t60.009999999999998\t70\t\n")

        filename = filename.replace('_adjustment', '_rate')
        with open(filename, 'w') as rtdata:
            rtdata.write("Is skipped\n")
            rtdata.write("592005597\t275387\t332\t60\t4.000\t-.375\n")
            rtdata.write("592005635\t278474\t332\t30\t2.250\t1.250\n")
            rtdata.write("592005636\t278474\t332\t30\t2.375\t1.000\n")
            rtdata.write("592005637\t278474\t332\t30\t2.500\t.750\n")
            rtdata.write("592005638\t278474\t332\t30\t2.625\t.500\n")
            rtdata.write("592005639\t278474\t332\t30\t2.750\t.250\n")
            rtdata.write("592005640\t278474\t332\t30\t2.875\t.000\n")

        filename = filename.replace('_rate', '_region')
        with open(filename, 'w') as regdata:
            regdata.write("Is skipped\n")
            regdata.write("12\tAK\tTrue\n")
            regdata.write("12\tAL\tFalse\n")
            regdata.write("12\tAR\tFalse\n")
            regdata.write("12\tAZ\tFalse\n")

        arch_name = '%s.zip' % date
        zfile = zipfile.ZipFile(arch_name, 'w')
        for key in ['product', 'adjustment', 'rate', 'region']:
            zfile.write('%s_%s.txt' % (date, key))
        zfile.close()
        return zipfile.ZipFile(arch_name, 'r')
